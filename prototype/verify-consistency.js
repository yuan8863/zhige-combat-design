/* 三方一致性核验：原型 vs 设计文档（01 白皮书 + 03 技能集）
   用法：在浏览器控制台粘贴运行。期望 24 招式 × 6 字段 + 有利帧 + 8 技能，全部通过。 */
(() => {
  const HERO = {
    zhirui:   { dmg:1.00, poise:1.00, off:0,  range:140, invuln:8,
      m:{ l1:[6,3,9,18,62,14], l2:[7,3,12,22,78,16], l3:[10,4,20,34,115,26],
          heavy:[18,5,24,47,185,38], launch:[12,4,20,36,95,20], ult:[24,6,30,60,380,100] },
      sk:{ s1:[5,8,11,24], s2:[4,20,6,30] } },
    zhongfeng:{ dmg:1.18, poise:1.44, off:4, range:175, invuln:8, armorFrom:8,
      m:{ l1:[10,3,9,22,73,20], l2:[11,3,12,26,92,23], l3:[14,4,20,38,136,37],
          heavy:[22,5,24,51,218,55], launch:[16,4,20,40,112,29], ult:[28,6,32,66,470,150] },
      sk:{ s1:[12,15,8,35], s2:[20,6,26,52] } },
    yingsuo:  { dmg:0.82, poise:0.80, off:-1, range:115, invuln:10,
      m:{ l1:[5,3,9,17,51,11], l2:[6,3,12,21,64,13], l3:[9,4,20,33,94,21],
          heavy:[17,5,24,46,152,30], launch:[11,4,20,35,78,16], ult:[20,18,30,68,62,15] },
      sk:{ s1:[2,4,0,6], s2:[4,27,6,37] } },
    changce:  { dmg:0.98, poise:0.96, off:2, range:225, invuln:8,
      m:{ l1:[8,3,9,20,61,13], l2:[9,3,12,24,76,15], l3:[12,4,20,36,113,25],
          heavy:[20,5,24,49,181,36], launch:[14,4,20,38,93,19], ult:[26,5,34,65,350,60] },
      sk:{ s1:[8,12,10,30], s2:[18,0,10,28] } },
  };
  const ADV = { l1:[2,0], l2:[-1,-3], l3:[-2,-8], heavy:[-7,-5], launch:[0,-8] };
  const F = ['startup','active','recovery','total','dmg','poise'];
  const fails = []; let n = 0;
  for (const h of HEROES) {
    const e = HERO[h.id];
    if (Math.abs(h.dmgScale - e.dmg) > 1e-9)   fails.push(h.name + '.dmgScale');
    if (Math.abs(h.poiseScale - e.poise) > 1e-9) fails.push(h.name + '.poiseScale');
    if (h.range !== e.range)      fails.push(h.name + '.range');
    if (h.invulnFrames !== e.invuln) fails.push(h.name + '.invuln');
    if (e.armorFrom !== undefined && h.armorFrom !== e.armorFrom) fails.push(h.name + '.armorFrom');
    const mv = buildMoves(h);
    for (const k of ['l1','l2','l3','heavy','launch','ult']) {
      const m = mv[k];
      F.forEach((f,i) => { n++; if (m[f] !== e.m[k][i]) fails.push(`${h.name}.${k}.${f} ${m[f]}≠${e.m[k][i]}`); });
      if (ADV[k]) { n += 2;
        if (m.advHit !== ADV[k][0])   fails.push(`${h.name}.${k}.advHit ${m.advHit}≠${ADV[k][0]}`);
        if (m.advBlock !== ADV[k][1]) fails.push(`${h.name}.${k}.advBlock ${m.advBlock}≠${ADV[k][1]}`); }
    }
    for (const k of ['s1','s2']) {
      const m = mv[k];
      if (!m) { fails.push(`${h.name}.${k} 缺失`); continue; }
      ['startup','active','recovery','total'].forEach((f,i) => {
        n++; if (m[f] !== e.sk[k][i]) fails.push(`${h.name}.${k}.${f} ${m[f]}≠${e.sk[k][i]}`);
      });
    }
  }
  return {
    核验字段数: n,
    结果: fails.length ? '❌ 失败' : '✅ 全部通过',
    失败项: fails.slice(0, 10),
    技能: HEROES.map(h => { const m = buildMoves(h);
      return h.name + ': ' + m.s1.name + ' ' + m.s1.total + 'f CD' + (m.s1.cd/60) + 's / '
           + m.s2.name + ' ' + m.s2.total + 'f CD' + (m.s2.cd/60) + 's'; }),
  };
})()
