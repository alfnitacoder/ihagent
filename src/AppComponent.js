import React, { useRef, useState } from 'react';
import { scanPdf, RULES, SEVERITY } from './pdfScanner';
import { makeMaliciousPdf, makeCleanPdf } from './makeSamplePdf';

const LEVEL_META = {
  clean: { label: 'CLEAN', blurb: 'No dangerous features found in this document.' },
  low: { label: 'LOW RISK', blurb: 'Minor features present. Review before opening.' },
  medium: { label: 'CAUTION', blurb: 'This document can act on its own. Do not open in a full-featured reader.' },
  high: { label: 'DANGEROUS', blurb: 'Strong indicators of a malicious document. Do not open.' },
  critical: { label: 'THREAT', blurb: 'This document contains known backdoor vectors. Do not open — treat as hostile.' },
};

function formatBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(2) + ' MB';
}

function Verdict({ report }) {
  const meta = LEVEL_META[report.level] || LEVEL_META.clean;
  return (
    <section className={'verdict verdict-' + report.level}>
      <div className="verdict-stamp" aria-hidden="true">{meta.label}</div>
      <div className="verdict-body">
        <p className="verdict-file">{report.fileName} <span className="verdict-size">{formatBytes(report.size)}</span></p>
        <p className="verdict-blurb">{meta.blurb}</p>
        <p className="verdict-meta">
          {report.findings.length} finding{report.findings.length === 1 ? '' : 's'} ·
          risk score {report.score} · {report.streamsScanned} compressed stream{report.streamsScanned === 1 ? '' : 's'} decompressed
        </p>
      </div>
    </section>
  );
}

function FindingRow({ f }) {
  const meta = SEVERITY[f.severity];
  return (
    <li className={'finding finding-' + f.severity}>
      <span className="finding-sev">{meta.label}</span>
      <div className="finding-main">
        <h3>
          {f.name}
          {f.hiddenInStreams && <em className="finding-hidden">hidden in compressed stream</em>}
        </h3>
        <p>{f.why}</p>
      </div>
      <span className="finding-count" title="occurrences">{f.count}×</span>
    </li>
  );
}

function App() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  async function handleFile(file) {
    setError('');
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      setError('File is larger than 50 MB — too big to scan in the browser.');
      return;
    }
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      const r = scanPdf(buf, file.name);
      if (!r.isPdf) {
        setError('That file does not start with %PDF — it is not a PDF document.');
        setReport(null);
        return;
      }
      setReport(r);
    } catch (e) {
      setError('Could not read that file: ' + e.message);
    }
  }

  function runSample(kind) {
    setError('');
    const bytes = kind === 'malicious' ? makeMaliciousPdf() : makeCleanPdf();
    setReport(scanPdf(bytes, kind === 'malicious' ? 'sample-malicious.pdf' : 'sample-clean.pdf'));
  }

  return (
    <div className="sentinel">
      <div className="backdrop" aria-hidden="true" />

      <header className="hero">
        <p className="hero-kicker">Defensive tooling — runs 100% in your browser</p>
        <h1 className="hero-brand">PDF SENTINEL</h1>
        <p className="hero-headline">See the backdoor before the PDF opens it.</p>
        <p className="hero-sub">
          Drop a PDF and we dissect its raw structure — JavaScript, launch
          actions, embedded files, payloads hidden in compressed streams —
          and tell you exactly what it can do to your machine.
        </p>
        <div className="hero-cta">
          <button className="btn btn-solid" onClick={() => inputRef.current && inputRef.current.click()}>
            Scan a PDF
          </button>
          <button className="btn btn-ghost" onClick={() => runSample('malicious')}>
            Run the infected sample
          </button>
        </div>
      </header>

      <main className="scan">
        <div
          className={'dropzone' + (dragOver ? ' dropzone-over' : '')}
          onDragOver={e => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={e => {
            e.preventDefault();
            setDragOver(false);
            handleFile(e.dataTransfer.files && e.dataTransfer.files[0]);
          }}
          onClick={() => inputRef.current && inputRef.current.click()}
          role="button"
          tabIndex={0}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') inputRef.current && inputRef.current.click(); }}
        >
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={e => { handleFile(e.target.files && e.target.files[0]); e.target.value = ''; }}
          />
          <p className="dropzone-title">Drop a PDF here, or click to browse</p>
          <p className="dropzone-sub">Nothing is uploaded — the file never leaves this tab.</p>
        </div>

        {error && <p className="scan-error">{error}</p>}

        {report && (
          <div className="report">
            <Verdict report={report} />
            {report.findings.length > 0 ? (
              <ol className="findings">
                {report.findings.map(f => <FindingRow key={f.id} f={f} />)}
              </ol>
            ) : (
              <p className="clean-note">
                No JavaScript, launch actions, embedded files, or other
                executable features were found. This does not guarantee the
                document is safe — it means it has no obvious way to run code.
              </p>
            )}
            <div className="report-actions">
              <button className="btn btn-ghost" onClick={() => runSample('malicious')}>Infected sample</button>
              <button className="btn btn-ghost" onClick={() => runSample('clean')}>Clean sample</button>
              <button className="btn btn-ghost" onClick={() => { setReport(null); setError(''); }}>Clear</button>
            </div>
          </div>
        )}
      </main>

      <section className="vectors" id="vectors">
        <h2>What we look for</h2>
        <p className="vectors-intro">
          A PDF is a structured file, and the spec includes features that let a
          document <em>act</em> — run scripts, launch programs, ship files.
          These are the features a backdoored PDF abuses, and the ones this
          scanner flags.
        </p>
        <ul className="vector-list">
          {RULES.map(r => (
            <li key={r.id} className={'vector vector-' + r.severity}>
              <span className="vector-sev">{SEVERITY[r.severity].label}</span>
              <div>
                <h3>{r.name}</h3>
                <p>{r.why}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="defend">
        <h2>How to defend</h2>
        <ol className="defend-list">
          <li>
            <strong>Scan before you open.</strong> Run documents from unknown
            senders through a structural scanner like this one — or a
            sandboxed PDF parser — before a full-featured reader ever sees them.
          </li>
          <li>
            <strong>Disable JavaScript in your reader.</strong> Acrobat,
            Preview, and browsers all have a setting to turn off PDF
            JavaScript. With it off, the most common backdoor vector simply
            cannot fire.
          </li>
          <li>
            <strong>Strip on upload.</strong> If your app accepts PDF uploads
            (documents, invoices, IDs), re-render them server-side — parse with
            a library that ignores actions and scripts, and store the clean
            output. Never serve the original bytes.
          </li>
          <li>
            <strong>Isolate the render.</strong> Preview uploads in a
            sandboxed iframe or a headless renderer with no network, no
            filesystem, and no plugins — so even a missed vector has nowhere
            to go.
          </li>
          <li>
            <strong>Block the escape routes.</strong> At the network layer,
            block the browser from launching local programs and restrict
            outbound requests from the document viewer. A PDF that can’t
            launch, submit, or phone home is just paper.
          </li>
        </ol>
      </section>

      <footer className="footer">
        <p>PDF Sentinel — structural PDF threat scanning, client-side. Built with React.</p>
      </footer>
    </div>
  );
}

export default App;