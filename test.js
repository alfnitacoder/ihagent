require('@babel/register')({
  presets: [
    ['@babel/preset-env', { modules: 'commonjs' }],
    ['@babel/preset-react', { runtime: 'classic' }],
  ],
  extensions: ['.js', '.jsx'],
  babelrc: false,
  configFile: false,
});

// SSR test does not need real CSS
require.extensions['.css'] = () => {};

const React = require('react');
const ReactDOMServer = require('react-dom/server');
const assert = require('assert');

const { scanPdf } = require('./src/pdfScanner');
const { makeMaliciousPdf, makeCleanPdf } = require('./src/makeSamplePdf');
const App = require('./src/AppComponent').default;

// 1. The "infected" sample is flagged as critical and its key vectors are found.
const mal = scanPdf(makeMaliciousPdf(), 'malicious.pdf');
assert(mal.level === 'critical', 'malicious PDF should be critical, got ' + mal.level);
const ids = mal.findings.map(f => f.id);
assert(ids.includes('javascript'), 'should detect embedded JavaScript');
assert(ids.includes('launch'), 'should detect launch action');
assert(ids.includes('embeddedfile'), 'should detect embedded file');
assert(mal.findings.some(f => f.hiddenInStreams), 'should find a payload hidden in a compressed stream');
assert(mal.isPdf === true, 'sample should be recognised as a PDF');

// 2. A clean PDF passes with no findings.
const clean = scanPdf(makeCleanPdf(), 'clean.pdf');
assert(clean.level === 'clean', 'clean PDF should be clean, got ' + clean.level);
assert(clean.findings.length === 0, 'clean PDF should have no findings');

// 3. The app renders (SSR smoke test).
const html = ReactDOMServer.renderToString(React.createElement(App));
assert(html.includes('PDF SENTINEL'), 'brand should render');
assert(html.includes('What we look for'), 'detection section should render');

console.log('All tests passed');
console.log('  malicious.pdf -> ' + mal.level + ' (score ' + mal.score + ', ' + mal.findings.length + ' findings)');
console.log('  clean.pdf     -> ' + clean.level + ' (score ' + clean.score + ', ' + clean.findings.length + ' findings)');