require('@babel/register')({
  presets: [
    ['@babel/preset-env', { modules: 'commonjs' }],
    ['@babel/preset-react', { runtime: 'classic' }],
  ],
  extensions: ['.js', '.jsx'],
  babelrc: false,
  configFile: false,
});

const fs = require('fs');
const path = require('path');
const { scanPdf, SEVERITY } = require('./src/pdfScanner');

const files = process.argv.slice(2);
if (files.length === 0) {
  console.log('Usage: node scan-cli.js <file.pdf> [more.pdf ...]');
  process.exit(1);
}

const bar = '─'.repeat(64);
let anyCritical = false;

for (const file of files) {
  const abs = path.resolve(file);
  if (!fs.existsSync(abs)) {
    console.log(`\n${bar}\nMISSING: ${file}\n${bar}`);
    continue;
  }
  const bytes = new Uint8Array(fs.readFileSync(abs));
  const r = scanPdf(bytes, path.basename(file));

  console.log(`\n${bar}`);
  console.log(`FILE: ${path.basename(file)}`);
  console.log(`SIZE: ${r.size} bytes   STREAMS DECOMPRESSED: ${r.streamsScanned}`);
  console.log(`VERDICT: ${r.level.toUpperCase()}   RISK SCORE: ${r.score}`);
  console.log(bar);

  if (!r.isPdf) {
    console.log('  ! Not a valid PDF (does not start with %PDF)');
    continue;
  }

  if (r.clean) {
    console.log('  ✓ No dangerous features found.');
  } else {
    for (const f of r.findings) {
      const tag = SEVERITY[f.severity].label.toUpperCase().padEnd(8);
      const hidden = f.hiddenInStreams ? '  [hidden in stream]' : '';
      console.log(`  ${tag} ${f.name}  ×${f.count}${hidden}`);
    }
  }

  if (r.level === 'critical' || r.level === 'high') anyCritical = true;
}

console.log(`\n${bar}`);
console.log(anyCritical ? '⚠ At least one file is DANGEROUS/THREAT.' : 'All scanned files are clean or low-risk.');
console.log(bar);