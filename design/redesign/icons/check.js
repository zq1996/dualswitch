const fs = require('fs');
const html = fs.readFileSync('/Users/zhqi/work/workbuddy/dualswitch/design/redesign/index-v2.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
console.log('script blocks:', scripts.length);
scripts.forEach((m, i) => {
  try { new Function(m[1]); console.log('  block', i, 'OK', m[1].length); }
  catch (e) { console.log('  block', i, 'FAIL', e.message); }
});
console.log('ICONS present:', html.includes('const ICONS'));
console.log('img emptyArt:', (html.match(/id="emptyArt/g) || []).length);
console.log('ICONS.typeText use:', (html.match(/ICONS\.typeText/g) || []).length);
console.log('ICONS.typeUrl use:', (html.match(/ICONS\.typeUrl/g) || []).length);
console.log('ICONS.emptyArt use:', (html.match(/ICONS\.emptyArt/g) || []).length);
console.log('div balance:', (html.match(/<div/g) || []).length, (html.match(/<\/div>/g) || []).length);
console.log('residual empty-art svg:', (html.match(/<svg class="empty-art"/g) || []).length);
