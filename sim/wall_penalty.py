#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墙面惩罚专项验证（白皮书 §8.2）
================================
设计主张：击退是攻击方的「安全垫」——A3 是 −2 帧劣势，但 110px 击退让对手需要
约 19 帧才能走回攻击距离，所以攻击方是安全的。**除非对手背后是墙**：
贴墙时击退被吸收，攻击方的负帧劣势直接暴露。

本脚本用 A/B 对照量化「贴墙」到底值多少胜率：
  对照组 —— 双方都在场地中央开阔处开局
  实验组 —— 一方贴左墙开局（另一方仍在开阔处）

若「贴墙 = 劣势」成立，实验组的贴墙方胜率应显著低于 50%。

运行：python _wall_penalty.py
"""
import sys
import random

sys.path.insert(0, '.')
import combat_balance_sim as S

PAIRS = [('zhirui', 'zhongfeng'), ('zhongfeng', 'yingsuo'),
         ('yingsuo', 'changce'), ('changce', 'zhirui')]
N = 1200
SK = S.SKILL_LEVELS['normal']


def run(a_x, b_x):
    rng = random.Random(31337)
    tot = 0.0
    for a, b in PAIRS:
        wins = 0
        for _ in range(N):
            w, _f, _st, _m = S.simulate(a, b, SK, SK, rng, 0.0, 60 * 240, a_x, b_x)
            if w == 'A':
                wins += 1
        tot += wins / float(N)
    return tot / len(PAIRS)


print('')
print('=== 墙面惩罚 A/B 对照（每对位 %d 局 × %d 对位 = %d 局/组）===' % (N, len(PAIRS), N * len(PAIRS)))
print('')

# 对照组：双方都在中央（A 在 440，B 在 840，两侧各留 350px）
c_base = run(440.0, 840.0)
# 实验组一：A 贴左墙（x=110，距墙 20px）
c_wallA = run(110.0, 840.0)
# 实验组二：B 贴右墙（x=1170）
c_wallB = run(440.0, 1170.0)
# 实验组三：双方都贴墙（对称，验证是否只是站位偏移造成）
c_both = run(110.0, 1170.0)

pen_a = (c_base - c_wallA) * 100      # A 贴墙 → A 胜率的下降量 = A 付出的代价
pen_b = (c_wallB - c_base) * 100      # B 贴墙 → A 胜率的上升量 = B 付出的代价
print('  对照组   双方开阔（A 440 / B 840）        A 胜率 %5.1f%%' % (c_base * 100))
print('  实验一   A 贴左墙（A 110 / B 840）        A 胜率 %5.1f%%   → A 贴墙代价 %+.1f pp'
      % (c_wallA * 100, -pen_a))
print('  实验二   B 贴右墙（A 440 / B 1170）       A 胜率 %5.1f%%   → B 贴墙代价 %+.1f pp'
      % (c_wallB * 100, -pen_b))
print('  实验三   双方都贴墙（A 110 / B 1170）     A 胜率 %5.1f%%   （对称性校验）'
      % (c_both * 100))
print('')

wall_cost = (pen_a + pen_b) / 2.0
print('  → 贴墙的平均胜率代价：%.1f 个百分点（A 侧 %.1f pp / B 侧 %.1f pp）'
      % (wall_cost, pen_a, pen_b))
if wall_cost > 3:
    print('  → 结论：✅ 「墙面惩罚」成立——贴墙是**可被测量**的劣势，且量级足够支撑')
    print('     「把对手逼到墙角」成为一个真实的战术目标（白皮书 §8.2 的设计主张得到数据支持）。')
elif wall_cost > 1:
    print('  → 结论：⚠️ 「墙面惩罚」存在但很弱（%.1f pp），不足以支撑「把对手逼到墙角」成为战术目标。' % wall_cost)
    print('     建议：加大击退距离，或让贴墙时 A3 的负帧劣势更明显地暴露。')
else:
    print('  → 结论：❌ 「墙面惩罚」在模型中未生效——贴墙不构成劣势。')
    print('     根因排查：竞技场边界 90~1190，击退后是否真的被 clamp？是否被防重叠逻辑抵消？')
print('')
