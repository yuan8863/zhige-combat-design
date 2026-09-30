#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《止戈》战斗平衡仿真 (Combat Balance Simulation)
================================================
原创作品 ④ · 黄渊 · 2027 届雷火「虚拟世界架构师（游戏战斗策划）」投递材料

用途
----
把《止戈》战斗系统设计白皮书中的平衡主张，用蒙特卡洛方法验证。
本脚本与可玩原型 `止戈-战斗原型.html` 使用**同一套帧级规则**，
包括起手/判定/收招、有利帧、格挡硬直、架崩、韧性、破防、处决、杀势。

验证的五个命题
--------------
  P1  四职业平均胜率落在 48%~52%           → 无绝对强职业
  P2  任一 1v1 对位胜率落在 42%~58%        → 软克制（而非硬克制）
  P3  各水平段 TTK 落在目标区间            → 新手 ≤15s / 中 16~22s / 高 ≤35s
  P4  落后 60 杀势时同水平胜率 35%~42%      → 反滚雪球有效但不抹平优势
  P5  技术提升 10% 能覆盖克制优势(≤8pp)     → 技术变量 > 克制优势

运行
----
    python combat_balance_sim.py                # 默认规模（约 3 万局）
    python combat_balance_sim.py --quick        # 快速抽查
    python combat_balance_sim.py --full         # 高精度（更慢）
    python combat_balance_sim.py --seed 42 --out result.json

纯标准库实现，无第三方依赖。
"""

import argparse
import json
import random
import sys
import time
from collections import OrderedDict

# =============================================================================
# 一、系统常量（与 01_战斗系统设计白皮书_止戈.md §3~§5 逐项对应）
# =============================================================================
FPS = 60
MAX_HP = 1200.0
MAX_POISE = 100.0
POISE_REGEN = 12.0 / FPS              # 每秒 12
POISE_REGEN_CORNERED = 18.0 / FPS     # 困兽之斗：每秒 18
BLOCK_DRAIN = 10.0 / FPS              # 格挡每秒耗韧 10
BLOCK_MAX_FRAMES = 45                 # 格挡最长 45 帧
PARRY_WINDOW = 8                      # 弹反窗口 8f（困兽 12f）
PARRY_WINDOW_CORNERED = 12
PARRY_MOMENTUM = 22                   # 弹反杀势 +22（困兽 ×1.5 → 33）
PARRY_MOMENTUM_CORNERED = 30
BLOCK_REDUCE = 0.80                   # 格挡减伤 80%
BLOCK_POISE_MULT = 0.5                # 格挡时削韧 ×0.5
GUARDCRUSH_POISE_MULT = 1.8           # 重击打中格挡时削韧 ×1.8（触发架崩）
BREAK_FRAMES = 40                     # 破防硬直 40f
EXECUTE_DMG = 300                     # 处决伤害
EXECUTE_DMG_CORNERED = 200
PARRY_POISE_CORNERED = 40              # 困兽之斗：弹反成功额外削对手 40 韧性
CORNERED_POISE_TAKEN = 0.75            # 困兽之斗：受到的削韧 ×0.75
BLOCK_REDUCE_CORNERED = 0.88           # 困兽之斗：格挡减伤 80% -> 88%（对标对方「掌控」的 +15% 伤害）
MOMENTUM_DECAY = 3.0 / FPS            # 无交战每秒衰减 3
MOMENTUM_DECAY_DELAY = 180            # 3 秒无交战才开始衰减
BLOCK_HITSTUN_LIGHT = 12              # 格挡硬直：轻档
BLOCK_HITSTUN_HEAVY = 16              # 格挡硬直：重档
GUARDCRUSH_STUN = 24                  # 架崩硬直

# 状态枚举（用整数以加速热循环）
S_IDLE, S_ATTACK, S_BLOCK, S_DODGE, S_HITSTUN, S_BREAK = range(6)

# =============================================================================
# 二、基础招式表（执锐 = 100% 基准）
# =============================================================================
BASE_MOVES = OrderedDict([
    # key          startup active recovery  dmg  poise  lunge hitstun blockstun knockback cancel tier  next
    ('l1',    dict(startup=6,  active=3, recovery=9,  dmg=62,  poise=14, hitstun=14,
                   blockstun=BLOCK_HITSTUN_LIGHT, cancel=4, next='l2', tier='light')),
    ('l2',    dict(startup=7,  active=3, recovery=12, dmg=78,  poise=16, hitstun=14,
                   blockstun=BLOCK_HITSTUN_LIGHT, cancel=4, next='l3', tier='light')),
    ('l3',    dict(startup=10, active=4, recovery=20, dmg=115, poise=26, hitstun=22,
                   blockstun=BLOCK_HITSTUN_HEAVY, cancel=0, next=None, tier='heavy')),
    ('heavy', dict(startup=18, active=5, recovery=24, dmg=185, poise=38, hitstun=22,
                   blockstun=GUARDCRUSH_STUN, cancel=0, next=None, tier='heavy',
                   guardCrush=True)),
    ('launch',dict(startup=12, active=4, recovery=20, dmg=95,  poise=20, hitstun=24,
                   blockstun=BLOCK_HITSTUN_HEAVY, cancel=0, next=None, tier='heavy')),
    ('ult',   dict(startup=24, active=6, recovery=30, dmg=380, poise=100, hitstun=30,
                   blockstun=GUARDCRUSH_STUN, cancel=0, next=None, tier='ult',
                   armor=True, guardCrush=True)),
])

# =============================================================================
# 三、四职业（偏移值取自 03_英雄技能设计集_止戈.md）
# =============================================================================
HEROES = OrderedDict([
    ('zhirui', dict(name='执锐', dmgScale=1.00, poiseScale=1.00, startupOffset=0,
                    range=140, walk=180, run=340, invulnFrames=8,
                    armorFrom=None, closePenalty=None, ult=None)),
    ('zhongfeng', dict(name='重锋', dmgScale=1.18, poiseScale=1.44, startupOffset=4,
                       range=175, walk=170, run=320, invulnFrames=8,
                       armorFrom=8, closePenalty=None, knockbackScale=1.15,
                       ult=dict(name='绝技·崩山', startup=28, active=6, recovery=32,
                                dmg=470, poise=150, hitstun=30,
                                blockstun=GUARDCRUSH_STUN, guardCrush=True, armor=True))),
    ('yingsuo', dict(name='影梭', dmgScale=0.82, poiseScale=0.80, startupOffset=-1,
                     range=115, walk=195, run=360, invulnFrames=10,
                     armorFrom=None, closePenalty=None, knockbackScale=0.85,
                     ult=dict(name='绝技·千影', startup=20, active=18, recovery=30,
                              dmg=62, poise=15, hitstun=8, blockstun=8,
                              hits=6, hitInterval=3, knockback=6))),
    ('changce', dict(name='长策', dmgScale=0.98, poiseScale=0.96, startupOffset=2,
                     range=225, walk=175, run=330, invulnFrames=8,
                     armorFrom=None, closePenalty=(70, 0.60), knockbackScale=1.70,
                     ult=dict(name='绝技·千里', startup=26, active=5, recovery=34,
                              dmg=350, poise=60, hitstun=30,
                              blockstun=GUARDCRUSH_STUN))),
])


# =============================================================================
# 三·B、武学技能（每职业 2 个，与原型 SKILLS 逐字段一致）
# =============================================================================
# dashTo        位移量（正=向前，负=向后）
# invuln        无敌帧窗口 [起, 止]（相对招式帧）
# counter       反击架势 {dmg, poise, hitstun}
# armorStance   架势窗口内免僵直
# buffNext      架势生效时给"下一次攻击"的伤害倍率
# rangeMult     攻击距离倍率
# landing       落地冲击 {frame, radius, dmg, knockback}
# zone          部署场域 {dist, radius, duration, dps, slow}
SKILLS = {
    '执锐': [
        dict(name='踏雪', type='dash', cd=360, startup=5, active=8, recovery=11,
             dashTo=180, invuln=(6, 13)),
        dict(name='回风', type='stance', cd=540, startup=4, active=20, recovery=6,
             counter=dict(dmg=140, poise=30, hitstun=22)),
    ],
    '重锋': [
        dict(name='镇岳', type='buff', cd=600, startup=12, active=15, recovery=8,
             armorStance=True, buffNext=1.40),
        dict(name='荡寇', type='aoe', cd=420, startup=20, active=6, recovery=26,
             dmg=150, poise=45, hitstun=22, blockstun=16, knockback=130, rangeMult=1.80),
    ],
    '影梭': [
        dict(name='影步', type='blink', cd=300, startup=2, active=4, recovery=0,
             dashTo=200, invuln=(1, 6)),
        dict(name='连环刺', type='multi', cd=480, startup=4, active=27, recovery=6,
             dmg=45, poise=12, hitstun=10, blockstun=8, knockback=20,
             hits=3, hitInterval=9),
    ],
    '长策': [
        dict(name='横驱', type='retreat', cd=420, startup=8, active=12, recovery=10,
             dashTo=-160, landing=dict(frame=22, radius=240, dmg=60, knockback=120)),
        dict(name='枪阵', type='deploy', cd=720, startup=18, active=0, recovery=10,
             zone=dict(dist=150, radius=170, duration=480, dps=50, slow=0.35)),
    ],
}


def skill_kind(m):
    """技能按用途分类，决定 AI 什么时候该放它（与原型 aiSkillKind 一致）"""
    if m.get('counter') or m.get('armorStance'):
        return 'defensive'      # 回风 / 镇岳
    if m.get('zone') or m.get('landing'):
        return 'control'        # 枪阵 / 横驱
    if m.get('hits', 1) > 1 or m.get('rangeMult'):
        return 'offensive'      # 连环刺 / 荡寇
    return 'mobility'           # 踏雪 / 影步


def build_moves(h):
    """由基础招式 + 职业偏移生成完整招式表（与原型 buildMoves 完全一致）"""
    out = {}
    for k, b in BASE_MOVES.items():
        m = dict(b)
        if k == 'ult' and h['ult']:
            m.update(h['ult'])
        else:
            m['startup'] = b['startup'] + h['startupOffset']
            m['dmg'] = round(b['dmg'] * h['dmgScale'])
            m['poise'] = round(b['poise'] * h['poiseScale'])
        m.setdefault('hits', 1)
        m.setdefault('hitInterval', 1)
        m.setdefault('guardCrush', False)
        m.setdefault('armor', False)
        m['total'] = m['startup'] + m['active'] + m['recovery']
        # 有利帧 = 受击僵直/格挡硬直 − (总帧 − 起手)；active+recovery 恒定 → 跨职业不变
        m['advHit'] = m['hitstun'] - (m['active'] + m['recovery'])
        m['advBlock'] = m['blockstun'] - (m['active'] + m['recovery'])
        out[k] = m
    # 武学技能：与原型 SKILLS 逐字段一致
    for i, s in enumerate(SKILLS.get(h['name'], [])):
        m = dict(dmg=0, poise=0, hitstun=14, blockstun=12, knockback=0,
                 lunge=0, cancel=0, hits=1, hitInterval=1, tier='skill',
                 guardCrush=False, armor=False)
        m.update(s)
        m['key'] = 's%d' % (i + 1)
        m['total'] = m['startup'] + m['active'] + m['recovery']
        m['advHit'] = m['hitstun'] - (m['active'] + m['recovery'])
        m['advBlock'] = m['blockstun'] - (m['active'] + m['recovery'])
        out[m['key']] = m
    return out


# =============================================================================
# 四、技术水平（Skill Profile）
# =============================================================================
# reaction     : 对敌方起手做出防守决策所需帧数（越小越强）
# block_rate   : 反应后选择格挡（并期望吃到弹反窗口）的比例
# dodge_rate   : 选择闪避的比例
# trade_rate   : 选择抢帧（同时出招）的比例
# aggression   : 在攻击距离内主动进攻的倾向（★ 随技术水平【下降】：高手更耐心）
# regroup_poise: 韧性低于此值时主动退避重整（★ 随技术水平【上升】：高手会管理风险）
# combo_rate   : 命中后继续接续连段的纪律性
SKILL_LEVELS = {
    'novice': dict(label='新手', reaction=22, block_rate=0.18, dodge_rate=0.05,
                   trade_rate=0.06, aggression=0.62, combo_rate=0.35,
                   punish_rate=0.10, crush_rate=0.08, predict_rate=0.05, skill_rate=0.35,
                   regroup_poise=0.0, tempo=0.30),
    'normal': dict(label='中水平', reaction=14, block_rate=0.44, dodge_rate=0.15,
                   trade_rate=0.14, aggression=0.48, combo_rate=0.62,
                   punish_rate=0.28, crush_rate=0.22, predict_rate=0.20, skill_rate=0.70,
                   regroup_poise=28.0, tempo=0.85),
    'expert': dict(label='高水平', reaction=9, block_rate=0.62, dodge_rate=0.24,
                   trade_rate=0.22, aggression=0.36, combo_rate=0.82,
                   punish_rate=0.48, crush_rate=0.40, predict_rate=0.36, skill_rate=1.00,
                   regroup_poise=50.0, tempo=1.40),
}


def shift_skill(base, delta):
    """技术整体上浮 delta（0.10 = 10%）——用于验证『技术变量能否覆盖克制优势』"""
    s = dict(base)
    s['reaction'] = max(4, int(round(base['reaction'] * (1 - delta))))
    s['block_rate'] = min(0.92, base['block_rate'] * (1 + delta))
    s['dodge_rate'] = min(0.55, base['dodge_rate'] * (1 + delta))
    s['trade_rate'] = min(0.45, base['trade_rate'] * (1 + delta))
    s['combo_rate'] = min(0.97, base['combo_rate'] * (1 + delta))
    s['aggression'] = min(0.85, base['aggression'] * (1 + delta))
    s['punish_rate'] = min(0.80, base['punish_rate'] * (1 + delta))
    s['crush_rate'] = min(0.70, base['crush_rate'] * (1 + delta))
    s['predict_rate'] = min(0.65, base['predict_rate'] * (1 + delta))
    s['skill_rate'] = min(1.0, base['skill_rate'] * (1 + delta))
    s['regroup_poise'] = min(72.0, base['regroup_poise'] * (1 + delta))
    s['tempo'] = min(2.2, base['tempo'] * (1 + delta))
    return s


# =============================================================================
# 五、战士
# =============================================================================
class Fighter(object):
    __slots__ = ('h', 'moves', 'sk', 'x', 'facing', 'hp', 'poise', 'state',
                 'move', 'mf', 'hitseg', 'lasthit', 'stun', 'blockf', 'parry_until',
                 'block_until', 'dodgef', 'dodge_dir', 'movedir', 'thinkcd',
                 'invuln_until', 'threat_at', 'threat_move', 'threat_seen',
                 'buf_move', 'buf_at', 'is_p', 'armored',
                 'cd', 'stance', 'next_atk_mult')

    def __init__(self, hero_key, skill, x, facing, is_p):
        self.h = HEROES[hero_key]
        self.moves = build_moves(self.h)
        self.sk = skill
        self.x = float(x)
        self.facing = facing
        self.is_p = is_p
        self.reset()

    def reset(self):
        self.hp = MAX_HP
        self.poise = MAX_POISE
        self.state = S_IDLE
        self.move = None
        self.mf = 0
        self.hitseg = 0
        self.lasthit = -99
        self.stun = 0
        self.blockf = 0
        self.parry_until = -999
        self.block_until = -999
        self.dodgef = 0
        self.dodge_dir = -1
        self.movedir = 0
        self.thinkcd = 0
        self.invuln_until = -999
        self.threat_at = -9999
        self.threat_move = None
        self.threat_seen = None
        self.buf_move = None
        self.buf_at = -9999
        self.cd = {}                 # 技能冷却剩余帧
        self.stance = None           # 当前生效的架势（反击 / 霸体）
        self.next_atk_mult = 1.0     # 镇岳：下一次攻击伤害倍率
        self.armored = False

    # --- 由杀势推导的增益 ---
    def mods(self, momentum):
        a = momentum if self.is_p else -momentum
        if a >= 90:
            return 1.25, 1.12, 1.20
        if a >= 60:
            return 1.15, 1.08, 1.15
        if a >= 30:
            return 1.08, 1.04, 1.10
        return 1.00, 1.00, 1.00

    def cornered(self, momentum):
        a = momentum if self.is_p else -momentum
        return a <= -60

    def dominant(self, momentum):
        a = momentum if self.is_p else -momentum
        return a >= 60

    def parry_win(self, momentum):
        return PARRY_WINDOW_CORNERED if self.cornered(momentum) else PARRY_WINDOW

    def can_act(self):
        if self.state in (S_IDLE, S_BLOCK):
            return True
        if self.state == S_ATTACK and self.move and self.mf >= self.move['startup'] + self.move['active'] \
                and self.move['cancel'] > 0 and self.hitseg > 0:
            return True
        return False


# =============================================================================
# 六、单局仿真
# =============================================================================
def simulate(a_key, b_key, a_skill, b_skill, rng, momentum0=0.0, max_frames=60 * 240,
             a_x=440.0, b_x=840.0):
    """返回 (赢家 'A'/'B'/'draw', 帧数, 统计字典)

    a_x / b_x 可指定起始站位，用于验证「墙面惩罚」（白皮书 §8.2）：
    把一方放在靠近左右墙的位置，观察贴墙是否真的构成劣势。
    """
    A = Fighter(a_key, a_skill, float(a_x), 1, True)
    B = Fighter(b_key, b_skill, float(b_x), -1, False)
    momentum = float(momentum0)
    last_combat = -99999

    st = dict(hits=0, blocks=0, dodges=0, parries=0, breaks=0, execs=0,
              ults=0, whiffs=0, counters=0, skills=0, a_dmg=0.0, b_dmg=0.0)
    zones = []          # 部署场域（长策「枪阵」）

    def start_move(f, key, frame):
        m = f.moves[key]
        if m.get('cd'):                       # 技能：冷却未好则拒绝
            if f.cd.get(key, 0) > 0:
                return False
            f.cd[key] = m['cd']
        f.move = m
        f.mf = 0
        f.hitseg = 0
        f.lasthit = -99
        f.state = S_ATTACK
        if key == 'ult':
            nonlocal momentum
            momentum += -60.0 if f.is_p else 60.0
        if m.get('buffNext'):
            f.next_atk_mult = m['buffNext']
        return True

    def receive_hit(att, dfn, m, blocked, parried, frame):
        nonlocal momentum, last_combat
        last_combat = frame

        # --- 反击架势（执锐「回风」）：架势窗口内受击 → 自动反击 ---
        if dfn.stance and dfn.stance.get('counter'):
            c = dfn.stance['counter']
            att.hp = max(0.0, att.hp - c['dmg'])
            att.poise = max(0.0, att.poise - c['poise'])
            att.state = S_HITSTUN
            att.stun = c['hitstun']
            att.move = None
            att.hitseg = 0
            dfn.stance = None
            momentum += 12.0 if dfn.is_p else -12.0
            st['counters'] += 1
            return

        # --- 弹反 ---
        if parried:
            st['parries'] += 1
            gain = PARRY_MOMENTUM_CORNERED if dfn.cornered(momentum) else PARRY_MOMENTUM
            momentum += gain if dfn.is_p else -gain
            att.state = S_HITSTUN
            att.stun = 22
            att.move = None
            att.hitseg = 0
            # 困兽之斗：弹反附带削韧，让弹反成为真正的翻盘引擎
            if dfn.cornered(momentum):
                att.poise = max(0.0, att.poise - PARRY_POISE_CORNERED)
            return

        # --- 破防中：处决 ---
        if dfn.state == S_BREAK:
            ex = EXECUTE_DMG_CORNERED if dfn.cornered(momentum) else EXECUTE_DMG
            dfn.hp = max(0.0, dfn.hp - ex)
            dfn.stun = 0
            dfn.state = S_IDLE
            dfn.poise = MAX_POISE * 0.30
            st['execs'] += 1
            if att.is_p:
                st['a_dmg'] += ex
            else:
                st['b_dmg'] += ex
            return

        dmg_m, spd_m, poise_m = att.mods(momentum)
        dmg = m['dmg'] * dmg_m
        if m is att.moves['l2']:
            dmg *= 0.95
        elif m is att.moves['l3']:
            dmg *= 0.90
        # 镇岳增益：下一次基础攻击 ×1.4，用后即消耗
        if att.next_atk_mult > 1.0 and m['tier'] != 'skill':
            dmg *= att.next_atk_mult
            att.next_atk_mult = 1.0
        cp = att.h['closePenalty']
        if cp and abs(att.x - dfn.x) < cp[0]:
            dmg *= cp[1]
        pd = m['poise'] * poise_m
        # 困兽之斗：连续型防守补偿——切断「被压制 → 破防 → 处决」的雪球链条
        if dfn.cornered(momentum):
            pd *= CORNERED_POISE_TAKEN
        if blocked:
            br = BLOCK_REDUCE_CORNERED if dfn.cornered(momentum) else BLOCK_REDUCE
            dmg *= (1.0 - br)
            pd *= GUARDCRUSH_POISE_MULT if m['guardCrush'] else BLOCK_POISE_MULT
            st['blocks'] += 1
        else:
            st['hits'] += 1

        dfn.hp = max(0.0, dfn.hp - dmg)
        dfn.poise = max(0.0, dfn.poise - pd)
        if att.is_p:
            st['a_dmg'] += dmg
        else:
            st['b_dmg'] += dmg

        # --- 杀势 ---
        if blocked:
            mg = 3
        elif m['hits'] > 1:
            mg = 3
        elif m['tier'] == 'ult':
            mg = 14
        elif m['tier'] == 'heavy' and m is att.moves['heavy']:
            mg = 10
        elif m is att.moves['launch']:
            mg = 7
        else:
            mg = 6
        momentum += mg if att.is_p else -mg

        # --- 硬直（霸体：吸收轻档攻击的僵直） ---
        armor_holds = dfn.armored and m['tier'] == 'light'
        if armor_holds:
            pass                                  # 掉血掉韧，但不进入硬直
        elif blocked and m['guardCrush']:
            dfn.state = S_HITSTUN
            dfn.stun = GUARDCRUSH_STUN
            dfn.blockf = 0
        else:
            dfn.state = S_HITSTUN
            dfn.stun = m['blockstun'] if blocked else m['hitstun']
        if not blocked and not armor_holds:
            dfn.x += m.get('knockback', 0) * att.h.get('knockbackScale', 1.0) * att.facing

        # --- 韧性归零 → 破防 ---
        if dfn.poise <= 0 and dfn.state != S_BREAK:
            dfn.state = S_BREAK
            dfn.stun = BREAK_FRAMES
            dfn.move = None
            st['breaks'] += 1

    for frame in range(1, max_frames + 1):
        # ---------- 决策 ----------
        for me, opp in ((A, B), (B, A)):
            if me.stun > 0 or me.state in (S_HITSTUN, S_BREAK):
                me.threat_move = None
                continue
            dist = abs(opp.x - me.x)
            dirto = 1 if opp.x > me.x else -1
            my_range = me.h['range']

            # --- 移动意图：每帧刷新（格挡中也可缓慢推进，否则远程压制无解） ---
            if me.state in (S_IDLE, S_BLOCK):
                if me.poise < me.sk['regroup_poise'] and dist < my_range * 1.6:
                    # 退避重整：韧性见底先拉开距离等回复——这是"高手防守更好"的行为载体
                    me.movedir = -dirto
                elif dist > my_range * 0.85:
                    me.movedir = dirto
                elif dist < my_range * 0.70 and me.h['closePenalty']:
                    # 空间控制型：必须把对手挡在"我能打到你、你打不到我"的距离带里
                    me.movedir = -dirto
                else:
                    me.movedir = 0
            else:
                me.movedir = 0

            opp_atk = opp.state == S_ATTACK and opp.move
            opp_startup = bool(opp_atk and opp.mf <= opp.move['startup'])
            opp_recover = bool(opp_atk and opp.mf > opp.move['startup'] + opp.move['active'])

            # 威胁识别（仅起手阶段构成威胁）
            if opp_startup and dist <= opp.h['range'] + 16:
                tag = (opp.mf, id(opp.move))
                if me.threat_seen != tag:
                    me.threat_seen = tag
                    me.threat_at = frame
                    me.threat_move = opp.move
            elif not opp_atk:
                me.threat_move = None
                me.threat_seen = None

            # 反应延迟到达 → 防守决策
            if me.threat_move is not None and frame - me.threat_at >= me.sk['reaction']:
                tm = me.threat_move
                me.threat_move = None
                if me.can_act() and dist <= opp.h['range'] + 16 and me.state != S_DODGE:
                    r = rng.random()
                    if r < me.sk['block_rate'] * 0.55:          # 其中一部分瞄准弹反窗口
                        if me.state != S_BLOCK:
                            me.state = S_BLOCK
                            me.blockf = 0
                            me.parry_until = frame + me.parry_win(momentum)
                        me.block_until = frame + tm['total']
                    elif r < me.sk['block_rate'] * 0.55 + me.sk['dodge_rate']:
                        if me.can_act():
                            me.state = S_DODGE
                            me.dodgef = 0
                            # 被远程压制时向前闪避穿越距离带，否则向后拉开
                            me.dodge_dir = dirto if dist > my_range * 1.25 else -dirto
                    elif r < me.sk['block_rate'] * 0.55 + me.sk['dodge_rate'] + me.sk['trade_rate']:
                        if me.moves['l1']['startup'] < tm['startup']:
                            start_move(me, 'l1', frame)
                    else:
                        if me.state != S_BLOCK:
                            me.state = S_BLOCK
                            me.blockf = 0
                            me.parry_until = frame + me.parry_win(momentum)
                        me.block_until = frame + tm['total']

            # --- 惩罚对手收招：这是「有利帧」设计的核心兑现 ---
            # 必要条件：自身起手必须能塞进对手剩余的收招帧内，否则惩罚不成反被惩罚。
            # 这解释了为什么重锋（起手 10f）无法惩罚轻击（收招 9f），而影梭（3f）可以。
            opp_rec_left = (opp.move['total'] - opp.mf) if opp_recover else -1
            margin = opp_rec_left - me.moves['l1']['startup'] if opp_recover else -99
            # 有利帧余量越大越好惩罚：惩罚重击收招容易，惩罚轻击收招需要极高的帧精度
            p_punish = me.sk['punish_rate'] * (0.30 + min(1.0, max(0.0, margin / 10.0)))
            if (opp_recover and dist <= my_range and opp_rec_left >= me.moves['l1']['startup']
                    and me.state in (S_IDLE, S_BLOCK)
                    and rng.random() < p_punish):
                start_move(me, 'l1', frame)
                continue

            # 常规节拍（节拍与自身招式长度挂钩：出手越快，攻击机会越多）
            if me.thinkcd > 0:
                me.thinkcd -= 1
                continue
            me.thinkcd = max(3, int(me.moves['l1']['total'] * me.sk['tempo']))

            if me.state in (S_ATTACK, S_DODGE):
                continue

            # ---- 武学技能的使用决策（与原型 aiSkillKind 同源）----
            for skey in ('s1', 's2'):
                sm = me.moves.get(skey)
                if not sm or me.cd.get(skey, 0) > 0:
                    continue
                kind = skill_kind(sm)
                use = 0.0
                if kind == 'defensive':
                    use = 0.40 * me.sk['skill_rate'] if (me.threat_move and dist <= opp.h['range'] + 40) else 0.0
                elif kind == 'offensive':
                    use = 0.55 * me.sk['skill_rate'] if dist <= my_range * 1.2 else 0.0
                elif kind == 'control':
                    use = 0.45 * me.sk['skill_rate'] if dist <= my_range * 1.6 else 0.0
                else:                                   # mobility
                    use = 0.50 * me.sk['skill_rate'] if (dist > my_range * 1.5 or me.cornered(momentum)) else 0.0
                if use > 0 and rng.random() < use:
                    if start_move(me, skey, frame):
                        st['skills'] += 1
                        break

            if me.state == S_ATTACK:
                continue

            if dist <= my_range:
                me.movedir = 0
                # 重整中不进攻（韧性见底时强行开打＝送破防）
                if me.poise < me.sk['regroup_poise']:
                    continue
                r = rng.random()
                # 对手起手越快 → 反应越无效 → 越依赖预判型格挡（3f→1.0, 12f→0.0）
                fastness = max(0.0, min(1.0, 1.0 - (opp.moves['l1']['startup'] - 3) / 9.0))

                # 对手龟缩格挡 → 用重击架崩（兑现"重击 > 格挡"这一环）
                if opp.state == S_BLOCK and r < me.sk['crush_rate']:
                    start_move(me, 'heavy', frame)
                    continue

                if r < me.sk['aggression']:
                    if me.dominant(momentum):
                        start_move(me, 'ult', frame)
                        st['ults'] += 1
                    else:
                        r2 = rng.random()
                        if r2 < 0.50:
                            start_move(me, 'l1', frame)
                        elif r2 < 0.72 and me.sk['combo_rate'] > 0.45:
                            start_move(me, 'l2', frame)
                        elif r2 < 0.86:
                            start_move(me, 'launch', frame)
                        else:
                            start_move(me, 'heavy', frame)
                elif r < me.sk['aggression'] + 0.10 + me.sk['predict_rate'] * fastness:
                    # 预判型防守：对手起手越快，越无法靠反应防住，只能提前架好格挡
                    if me.state != S_BLOCK:
                        me.state = S_BLOCK
                        me.blockf = 0
                        me.parry_until = frame + me.parry_win(momentum)
                    me.block_until = frame + 30

        # ---------- 状态推进 ----------
        for me in (A, B):
            # 技能冷却
            for ck in me.cd:
                if me.cd[ck] > 0:
                    me.cd[ck] -= 1

            # 韧性回复
            regen = POISE_REGEN_CORNERED if me.cornered(momentum) else POISE_REGEN
            if me.state != S_BLOCK:
                me.poise = min(MAX_POISE, me.poise + regen)

            me.armored = False
            me.stance = None          # 架势窗口每帧重建，帧内有效

            if me.state == S_BREAK:
                me.stun -= 1
                if me.stun <= 0:
                    me.state = S_IDLE
                    me.poise = MAX_POISE * 0.35
                    me.invuln_until = frame + 20
                continue

            if me.stun > 0:
                me.stun -= 1
                if me.stun <= 0:
                    me.state = S_IDLE
                continue

            if me.state == S_DODGE:
                me.dodgef += 1
                iv0, iv1 = 5, 5 + me.h['invulnFrames'] - 1
                if iv0 <= me.dodgef <= iv1:
                    me.x += (160.0 / me.h['invulnFrames']) * me.dodge_dir
                if me.dodgef >= 20:
                    me.state = S_IDLE
                continue

            if me.state == S_ATTACK:
                me.mf += 1
                m = me.move
                if me.mf == m['startup']:
                    me.x += m.get('lunge', 0) * me.facing
                if m['armor'] or (me.h['armorFrom'] and m['tier'] == 'heavy'
                                  and me.mf >= me.h['armorFrom']):
                    me.armored = True

                # ---- 武学技能特殊处理 ----
                # 位移：踏雪前突 / 影步瞬移 / 横驱后跳
                if m.get('dashTo') and m['startup'] < me.mf <= m['startup'] + max(1, m['active']):
                    me.x += (m['dashTo'] / max(1, m['active'])) * me.facing
                # 无敌窗口
                ivw = m.get('invuln')
                if ivw and ivw[0] <= me.mf <= ivw[1]:
                    me.invuln_until = frame + 1
                # 架势窗口：反击（回风）/ 霸体（镇岳）
                if (m.get('counter') or m.get('armorStance')) \
                        and m['startup'] < me.mf <= m['startup'] + m['active']:
                    me.stance = m
                    if m.get('armorStance'):
                        me.armored = True
                # 部署场域（枪阵）
                if m.get('zone') and me.mf == m['startup'] + 1:
                    z = m['zone']
                    zones.append(dict(x=me.x + z['dist'] * me.facing, cfg=z,
                                      until=frame + z['duration'], owner=me, tick=0))
                # 落地冲击（横驱）
                lz = m.get('landing')
                if lz and me.mf == lz['frame']:
                    opp2 = B if me is A else A
                    if abs(opp2.x - me.x) <= lz['radius'] and opp2.state != S_BREAK:
                        blk2 = opp2.state == S_BLOCK
                        dd = lz['dmg'] * (1.0 - BLOCK_REDUCE) if blk2 else lz['dmg']
                        opp2.hp = max(0.0, opp2.hp - dd)
                        if not blk2:
                            opp2.state = S_HITSTUN
                            opp2.stun = 16
                            opp2.x += lz['knockback'] * me.facing
                        momentum += 5.0 if me.is_p else -5.0
                        if me.is_p:
                            st['a_dmg'] += dd
                        else:
                            st['b_dmg'] += dd
                # 连段取消
                if (me.hitseg > 0 and m['cancel'] > 0
                        and me.mf >= m['startup'] + m['active'] and m['next']
                        and me.buf_move == 'light' and frame - me.buf_at <= 8):
                    nxt = m['next']
                    me.buf_move = None
                    start_move(me, nxt, frame)
                    continue
                if me.mf >= m['total']:
                    me.state = S_IDLE
                    me.move = None
                continue

            if me.state == S_BLOCK:
                me.blockf += 1
                if not me.cornered(momentum):
                    me.poise -= BLOCK_DRAIN    # 困兽之斗：格挡不再消耗韧性
                if me.poise <= 0 or me.blockf >= BLOCK_MAX_FRAMES:
                    me.state = S_BREAK
                    me.stun = BREAK_FRAMES
                    me.move = None
                    st['breaks'] += 1
                    continue
                if frame > me.block_until:
                    me.state = S_IDLE
                    me.blockf = 0
                continue

            me.state = S_IDLE

        # ---------- 位移 ----------
        for me in (A, B):
            if me.movedir and me.state in (S_IDLE, S_BLOCK):
                _, spd, _ = me.mods(momentum)
                spd_mult = 0.45 if me.state == S_BLOCK else 1.0   # 格挡推进更慢
                # 枪阵减速
                for z in zones:
                    if z['owner'] is not me and abs(me.x - z['x']) <= z['cfg']['radius']:
                        spd_mult *= (1.0 - z['cfg']['slow'])
                me.x += me.movedir * me.h['run'] * 0.72 * spd * spd_mult / FPS
            me.x = max(90.0, min(1190.0, me.x))
        if abs(A.x - B.x) < 56:
            mid = (A.x + B.x) / 2.0
            s = 1 if A.x <= B.x else -1
            A.x = mid - 28 * s
            B.x = mid + 28 * s
        A.facing = 1 if A.x <= B.x else -1
        B.facing = 1 if B.x <= A.x else -1

        # ---------- 命中结算 ----------
        # 关键：先收集双方命中，再统一结算。
        # 若按固定顺序"边判边结算"，先结算的一方会在同帧交换中白赢，
        # 导致矩阵不对称（A/B 视角胜率之和不等于 100%）。
        pending = []
        for att, dfn in ((A, B), (B, A)):
            if att.state != S_ATTACK or not att.move:
                continue
            m = att.move
            if not (m['startup'] < att.mf <= m['startup'] + m['active']):
                continue
            if att.hitseg > 0:
                multi = m['hits'] > 1
                if not (multi and att.hitseg < m['hits']
                        and att.mf >= att.lasthit + m['hitInterval']):
                    continue
            if abs(dfn.x - att.x) > att.h['range'] * m.get('rangeMult', 1.0):
                continue

            # 无敌帧
            inv = ((dfn.state == S_DODGE and 5 <= dfn.dodgef <= 5 + dfn.h['invulnFrames'] - 1)
                   or frame < dfn.invuln_until)
            if inv:
                momentum += -4.0 if att.is_p else 4.0
                st['dodges'] += 1
                att.hitseg = m['hits']
                continue

            blocked = dfn.state == S_BLOCK and frame > dfn.parry_until
            parried = dfn.state == S_BLOCK and frame <= dfn.parry_until
            pending.append((att, dfn, m, blocked, parried))

        for att, dfn, m, blocked, parried in pending:
            att.hitseg += 1
            att.lasthit = att.mf
            receive_hit(att, dfn, m, blocked, parried, frame)

        # ---------- 场域结算（长策「枪阵」）----------
        for i in range(len(zones) - 1, -1, -1):
            z = zones[i]
            if frame > z['until']:
                zones.pop(i)
                continue
            tgt = B if z['owner'] is A else A
            if abs(tgt.x - z['x']) <= z['cfg']['radius'] and tgt.state != S_BREAK:
                z['tick'] += 1
                if z['tick'] % 60 == 0:
                    dd = float(z['cfg']['dps'])
                    tgt.hp = max(0.0, tgt.hp - dd)
                    if z['owner'].is_p:
                        st['a_dmg'] += dd
                    else:
                        st['b_dmg'] += dd

        # ---------- 杀势衰减与钳制 ----------
        if frame - last_combat > MOMENTUM_DECAY_DELAY:
            if momentum > 0:
                momentum = max(0.0, momentum - MOMENTUM_DECAY)
            elif momentum < 0:
                momentum = min(0.0, momentum + MOMENTUM_DECAY)
        momentum = max(-100.0, min(100.0, momentum))

        if A.hp <= 0 or B.hp <= 0:
            break

    if A.hp <= 0 and B.hp <= 0:
        return 'draw', frame, st, momentum
    if B.hp <= 0:
        return 'A', frame, st, momentum
    if A.hp <= 0:
        return 'B', frame, st, momentum
    return 'draw', frame, st, momentum


def win_rate(a_key, b_key, a_sk, b_sk, n, rng, momentum0=0.0):
    wins = 0
    frames = 0
    agg = dict(hits=0, blocks=0, dodges=0, parries=0, breaks=0, execs=0,
               ults=0, counters=0, skills=0)
    for _ in range(n):
        w, f, st, _m = simulate(a_key, b_key, a_sk, b_sk, rng, momentum0)
        if w == 'A':
            wins += 1
        frames += f
        for k in agg:
            agg[k] += st[k]
    return wins / float(n), frames / float(n), agg


# =============================================================================
# 七、四项验证
# =============================================================================
def verify_matrix(n, rng, verbose=True):
    """P2 反制矩阵 + P1 平均胜率"""
    keys = list(HEROES.keys())
    normal = SKILL_LEVELS['normal']
    matrix = {}
    for a in keys:
        for b in keys:
            if a == b:
                continue
            wr, fr, _ = win_rate(a, b, normal, normal, n, rng)
            matrix[(a, b)] = (wr, fr)
            if verbose:
                print('  %-4s vs %-4s  %5.1f%%   平均 %.1fs'
                      % (HEROES[a]['name'], HEROES[b]['name'], wr * 100, fr / 60.0))
    avg = {}
    for a in keys:
        vs = [matrix[(a, b)][0] for b in keys if b != a]
        avg[a] = sum(vs) / len(vs)
    return matrix, avg


def verify_ttk(n, rng, verbose=True):
    """P3 各水平段 TTK"""
    res = {}
    keys = list(HEROES.keys())
    for lvl, sk in SKILL_LEVELS.items():
        tot_f = 0
        tot_n = 0
        agg = dict(hits=0, blocks=0, dodges=0, parries=0, breaks=0, execs=0)
        for i, a in enumerate(keys):
            b = keys[(i + 1) % 4]
            _wr, fr, ag = win_rate(a, b, sk, sk, n, rng)
            tot_f += fr * n
            tot_n += n
            for k in agg:
                agg[k] += ag[k]
        res[lvl] = (tot_f / tot_n, agg, tot_n)
        if verbose:
            avg_f = tot_f / tot_n
            print('  %-6s TTK = %5.1fs' % (sk['label'], avg_f / 60.0))
    return res


def verify_cornered(n, rng, verbose=True):
    """P4 困兽之斗：落后 60 杀势时的翻盘率（同水平对局）"""
    keys = list(HEROES.keys())
    normal = SKILL_LEVELS['normal']
    tot = 0.0
    for i, a in enumerate(keys):
        b = keys[(i + 1) % 4]
        wr, _f, _ = win_rate(a, b, normal, normal, n, rng, momentum0=-60.0)
        tot += wr
        if verbose:
            print('  %-4s 落后60杀势 vs %-4s  胜率 %5.1f%%'
                  % (HEROES[a]['name'], HEROES[b]['name'], wr * 100))
    return tot / len(keys)


def verify_skill_covers_counter(n, rng, verbose=True):
    """P5 技术提升 10% 能否覆盖克制优势"""
    normal = SKILL_LEVELS['normal']
    better = shift_skill(normal, 0.10)
    pairs = [('zhirui', 'zhongfeng'), ('zhongfeng', 'yingsuo'), ('yingsuo', 'changce')]
    out = []
    for a, b in pairs:
        base_wr, _f, _ = win_rate(a, b, normal, normal, n, rng)
        # 劣势方技术提升 10%
        if base_wr >= 0.5:
            fix_wr, _f, _ = win_rate(a, b, normal, better, n, rng)   # B 提升
            improved = 1.0 - fix_wr
        else:
            fix_wr, _f, _ = win_rate(a, b, better, normal, n, rng)   # A 提升
            improved = fix_wr
        out.append((a, b, base_wr, improved))
        if verbose:
            print('  %-4s vs %-4s  原始 %5.1f%%  →  劣势方技术+10%% 后 %5.1f%%'
                  % (HEROES[a]['name'], HEROES[b]['name'], base_wr * 100, improved * 100))
    return out


# =============================================================================
# 八、主流程
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description='《止戈》战斗平衡仿真')
    ap.add_argument('--quick', action='store_true', help='快速抽查（每格 400 局）')
    ap.add_argument('--full', action='store_true', help='高精度（每格 8000 局）')
    ap.add_argument('--n', type=int, default=None, help='每格对局数')
    ap.add_argument('--seed', type=int, default=20260930)
    ap.add_argument('--out', type=str, default='sim_result.json')
    args = ap.parse_args()

    n = args.n if args.n else (400 if args.quick else (8000 if args.full else 2000))
    rng = random.Random(args.seed)
    t0 = time.time()

    print('=' * 74)
    print('《止戈》战斗平衡仿真   每格 %d 局   随机种子 %d' % (n, args.seed))
    print('=' * 74)

    print('\n[P2] 反制矩阵（目标：全部落在 42%% ~ 58%%）')
    matrix, avg = verify_matrix(n, rng)

    print('\n[P1] 各职业平均胜率（目标：48%% ~ 52%%）')
    for k, v in avg.items():
        flag = 'OK ' if 0.48 <= v <= 0.52 else '!! '
        print('  %s%-4s  %5.1f%%' % (flag, HEROES[k]['name'], v * 100))

    print('\n[P3] 各水平段 TTK（目标：新手<=15s / 中 16~22s / 高<=35s）')
    ttk = verify_ttk(n, rng)

    print('\n[P4] 困兽之斗：落后 60 杀势的翻盘率（目标：35%% ~ 42%%）')
    cornered = verify_cornered(n, rng)
    print('  → 平均翻盘率 %5.1f%%' % (cornered * 100))

    print('\n[P5] 技术变量 vs 克制优势（目标：技术+10%% 能覆盖克制）')
    sk = verify_skill_covers_counter(n, rng)

    # ---- 判定 ----
    def ok_p2():
        return all(0.42 <= v[0] <= 0.58 for v in matrix.values())

    def ok_p1():
        return all(0.48 <= v <= 0.52 for v in avg.values())

    def ok_p4():
        return 0.35 <= cornered <= 0.42

    print('\n' + '=' * 74)
    print('验证结论')
    print('=' * 74)
    print('  P1 四职业平均胜率 48%%~52%%      : %s' % ('通过' if ok_p1() else '未通过'))
    print('  P2 反制矩阵 42%%~58%%            : %s' % ('通过' if ok_p2() else '未通过'))
    print('  P3 各水平 TTK 落在目标区间       : 见上表')
    print('  P4 落后翻盘率 35%%~42%%          : %s (%.1f%%)'
          % ('通过' if ok_p4() else '未通过', cornered * 100))
    print('  P5 技术变量覆盖克制优势          : 见上表')

    # 抽样误差提示：小样本下个别对位会因噪声滑出区间，这不是设计问题
    se = (0.25 / n) ** 0.5 * 100
    if n < 800:
        print('')
        print('  ⚠️ 本次每格仅 %d 局，单格标准误约 ±%.1f pp（95%%置信区间 ±%.1f pp）。' % (n, se, se * 1.96))
        print('     小样本下个别对位可能因抽样噪声滑出 42%%~58%%，这不代表设计不达标。')
        print('     定稿结论请用：python combat_balance_sim.py --n 1500 --seed 20260930')
    else:
        print('')
        print('  （每格 %d 局，单格标准误约 ±%.1f pp）' % (n, se))

    print('\n总耗时 %.1f 秒' % (time.time() - t0))

    # ---- 落盘 ----
    out = dict(
        meta=dict(n_per_cell=n, seed=args.seed, heroes={k: v['name'] for k, v in HEROES.items()}),
        matrix={'%s>%s' % (a, b): dict(win_rate=round(v[0], 4), avg_frames=int(v[1]))
                for (a, b), v in matrix.items()},
        avg_win_rate={k: round(v, 4) for k, v in avg.items()},
        ttk={k: dict(seconds=round(v[0] / 60.0, 2), n=v[2], raw=v[1]) for k, v in ttk.items()},
        cornered_win_rate=round(cornered, 4),
        cornered_detail=None,
        skill_covers=[dict(a=a, b=b, base=round(x, 4), after=round(y, 4)) for a, b, x, y in sk],
    )
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('结果已写入 %s' % args.out)


if __name__ == '__main__':
    main()
