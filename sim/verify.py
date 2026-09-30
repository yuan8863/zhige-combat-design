#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方一致性核验（Python 端）
==========================
把仿真 build_moves() 算出的招式表与设计文档（01 白皮书 + 03 技能集）逐字段比对。
与原型端的 `原型/_一致性核验.js` 使用同一组期望值。

运行：  python _verify.py
"""
import sys
import io

sys.path.insert(0, '.')
import combat_balance_sim as S

# 期望值 = 03_英雄技能设计集 §2 定稿表（与原型端 _一致性核验.js 同源）
EXPECT = {
    'zhirui': dict(
        dmg=1.00, poise=1.00, off=0, range=140, invuln=8, armorFrom=None,
        m={'l1': (6, 3, 9, 18, 62, 14), 'l2': (7, 3, 12, 22, 78, 16),
           'l3': (10, 4, 20, 34, 115, 26), 'heavy': (18, 5, 24, 47, 185, 38),
           'launch': (12, 4, 20, 36, 95, 20), 'ult': (24, 6, 30, 60, 380, 100)},
        sk={'s1': (5, 8, 11, 24), 's2': (4, 20, 6, 30)}),
    'zhongfeng': dict(
        dmg=1.18, poise=1.44, off=4, range=175, invuln=8, armorFrom=8,
        m={'l1': (10, 3, 9, 22, 73, 20), 'l2': (11, 3, 12, 26, 92, 23),
           'l3': (14, 4, 20, 38, 136, 37), 'heavy': (22, 5, 24, 51, 218, 55),
           'launch': (16, 4, 20, 40, 112, 29), 'ult': (28, 6, 32, 66, 470, 150)},
        sk={'s1': (12, 15, 8, 35), 's2': (20, 6, 26, 52)}),
    'yingsuo': dict(
        dmg=0.82, poise=0.80, off=-1, range=115, invuln=10, armorFrom=None,
        m={'l1': (5, 3, 9, 17, 51, 11), 'l2': (6, 3, 12, 21, 64, 13),
           'l3': (9, 4, 20, 33, 94, 21), 'heavy': (17, 5, 24, 46, 152, 30),
           'launch': (11, 4, 20, 35, 78, 16), 'ult': (20, 18, 30, 68, 62, 15)},
        sk={'s1': (2, 4, 0, 6), 's2': (4, 27, 6, 37)}),
    'changce': dict(
        dmg=0.98, poise=0.96, off=2, range=225, invuln=8, armorFrom=None,
        m={'l1': (8, 3, 9, 20, 61, 13), 'l2': (9, 3, 12, 24, 76, 15),
           'l3': (12, 4, 20, 36, 113, 25), 'heavy': (20, 5, 24, 49, 181, 36),
           'launch': (14, 4, 20, 38, 93, 19), 'ult': (26, 5, 34, 65, 350, 60)},
        sk={'s1': (8, 12, 10, 30), 's2': (18, 0, 10, 28)}),
}

ADV = {'l1': (2, 0), 'l2': (-1, -3), 'l3': (-2, -8), 'heavy': (-7, -5), 'launch': (0, -8)}
FIELDS = ('startup', 'active', 'recovery', 'total', 'dmg', 'poise')
KEYMAP = {'执锐': 'zhirui', '重锋': 'zhongfeng', '影梭': 'yingsuo', '长策': 'changce'}

fails = []
n = 0
for hk, h in S.HEROES.items():
    e = EXPECT[KEYMAP[h['name']]]
    for field, want in (('dmgScale', e['dmg']), ('poiseScale', e['poise']),
                        ('startupOffset', e['off']), ('range', e['range']),
                        ('invulnFrames', e['invuln']), ('armorFrom', e['armorFrom'])):
        n += 1
        if (h[field] is None) != (want is None) or (
                h[field] is not None and abs(h[field] - want) > 1e-9):
            fails.append('%s.%s %s != %s' % (h['name'], field, h[field], want))
    mv = S.build_moves(h)
    for k in ('l1', 'l2', 'l3', 'heavy', 'launch', 'ult'):
        m = mv[k]
        for f, want in zip(FIELDS, e['m'][k]):
            n += 1
            if m[f] != want:
                fails.append('%s.%s.%s %s != %s' % (h['name'], k, f, m[f], want))
        if k in ADV:
            for f, want in (('advHit', ADV[k][0]), ('advBlock', ADV[k][1])):
                n += 1
                if m[f] != want:
                    fails.append('%s.%s.%s %s != %s' % (h['name'], k, f, m[f], want))
    for k in ('s1', 's2'):
        m = mv[k]
        for f, want in zip(('startup', 'active', 'recovery', 'total'), e['sk'][k]):
            n += 1
            if m[f] != want:
                fails.append('%s.%s.%s %s != %s' % (h['name'], k, f, m[f], want))

print('')
print('=== 仿真端三方一致性核验 ===')
print('  核验字段：%d' % n)
print('  结果：%s' % ('✅ 全部通过' if not fails else '❌ 失败'))
if fails:
    for f in fails[:15]:
        print('    ' + f)
print('')
print('  技能表：')
for hk, h in S.HEROES.items():
    mv = S.build_moves(h)
    print('    %-4s %s %df CD%ds  /  %s %df CD%ds' % (
        h['name'], mv['s1']['name'], mv['s1']['total'], mv['s1']['cd'] // 60,
        mv['s2']['name'], mv['s2']['total'], mv['s2']['cd'] // 60))
sys.exit(1 if fails else 0)
