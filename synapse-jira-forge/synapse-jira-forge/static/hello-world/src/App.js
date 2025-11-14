import React, { useState } from 'react';
import { invoke } from '@forge/bridge';

function App() {
  const [transcript, setTranscript] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const onGenerate = async () => {
    if (!transcript.trim()) {
      setError('Please paste a transcript first.');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const data = await invoke('generateSynapse', { transcript });
      setResult(data);
    } catch (e) {
      console.error(e);
      setError(e.message || 'Failed to generate.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16, fontFamily: 'sans-serif', fontSize: 14 }}>
      <h3>Synapse – Meeting transcript to Jira requirements</h3>
      <p>Paste a meeting transcript and generate structured requirements & BDD test cases.</p>

      <textarea
        style={{ width: '100%', minHeight: 120, marginBottom: 8 }}
        value={transcript}
        onChange={(e) => setTranscript(e.target.value)}
        placeholder="Paste your meeting transcript here..."
      />

      <div style={{ marginBottom: 8 }}>
        <button onClick={onGenerate} disabled={loading}>
          {loading ? 'Generating…' : 'Generate with Synapse'}
        </button>
      </div>

      {error && (
        <div style={{ color: 'red', marginBottom: 8 }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {result && (
        <div>
          <h4>Requirements ({result.requirements?.length || 0})</h4>
          {(result.requirements || []).map((req, idx) => (
            <div
              key={idx}
              style={{
                border: '1px solid #ddd',
                borderRadius: 4,
                padding: 8,
                marginBottom: 8
              }}
            >
              <strong>
                {idx + 1}. {req.title}
              </strong>
              <p>{req.description}</p>
              {req.acceptance_criteria && req.acceptance_criteria.length > 0 && (
                <p>
                  <strong>Acceptance criteria:</strong>{' '}
                  {req.acceptance_criteria.join(' | ')}
                </p>
              )}
            </div>
          ))}

          <h4>Test cases ({result.test_cases?.length || 0})</h4>
          {(result.test_cases || []).map((tc, idx) => (
            <div
              key={idx}
              style={{
                border: '1px solid #eee',
                borderRadius: 4,
                padding: 8,
                marginBottom: 8
              }}
            >
              <strong>
                {idx + 1}. {tc.title}
              </strong>
              <pre style={{ whiteSpace: 'pre-wrap' }}>{tc.gherkin}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default App;
