require('@babel/register')({
  presets: [['@babel/preset-env', { modules: 'commonjs' }]],
  extensions: ['.js', '.jsx'],
  babelrc: false,
  configFile: false,
});
const fs = require('fs');
const path = require('path');
const { makeMaliciousPdf, makeCleanPdf } = require('./src/makeSamplePdf');

const outDir = path.resolve(__dirname, 'samples');
fs.mkdirSync(outDir, { recursive: true });

const mal = path.join(outDir, 'sample-malicious.pdf');
const clean = path.join(outDir, 'sample-clean.pdf');
fs.writeFileSync(mal, makeMaliciousPdf());
fs.writeFileSync(clean, makeCleanPdf());

console.log('Wrote:');
console.log('  ' + mal + '  (' + fs.statSync(mal).size + ' bytes)');
console.log('  ' + clean + '  (' + fs.statSync(clean).size + ' bytes)');