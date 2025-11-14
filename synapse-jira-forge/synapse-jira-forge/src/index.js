import Resolver from '@forge/resolver';
import { fetch, env } from '@forge/api';

const resolver = new Resolver();

/**
 * Resolver method triggered from the Forge UI
 * It accepts:
 *   payload.transcript — the text from the UI textarea
 *   context.extension.issue.key — the current Jira issue
 */
resolver.define('generateSynapse', async ({ payload, context }) => {
  const transcript = payload?.transcript || '';
  const issueKey = context?.extension?.issue?.key || 'UNKNOWN';

  if (!transcript.trim()) {
    throw new Error('Transcript is empty');
  }

  // Get OpenAI API key from Forge Variables
  const apiKey = await env.get('OPENAI_API_KEY');
  if (!apiKey) {
    throw new Error('OPENAI_API_KEY not configured in Forge variables');
  }

  // Prompt for Synapse generation
  const prompt = `
You are Synapse, an AI assistant that converts meeting transcripts into Jira-friendly requirements and BDD test cases.

Return STRICTLY valid JSON with this exact shape:
{
  "requirements": [
    {
      "title": "string",
      "description": "string",
      "acceptance_criteria": ["Given ... When ... Then ..."]
    }
  ],
  "test_cases": [
    {
      "title": "string",
      "gherkin": "Scenario: ..."
    }
  ]
}

Do not include explanations.

Meeting transcript:
"""${transcript}"""
`;

  // OpenAI API body
  const body = {
    model: 'gpt-4.1-mini',
    messages: [
      { 
        role: 'system', 
        content: 'You generate structured Jira requirements and BDD test cases. Always answer with valid JSON only.' 
      },
      { role: 'user', content: prompt }
    ],
    temperature: 0.2
  };

  // Call OpenAI
  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const text = await response.text();
    console.error('OpenAI error:', text);
    throw new Error(`OpenAI error: ${response.status} – ${text}`);
  }

  const data = await response.json();
  const content = data.choices?.[0]?.message?.content || '{}';

  // Parse JSON returned from LLM
  let parsed;
  try {
    parsed = JSON.parse(content);
  } catch (err) {
    console.error('LLM returned invalid JSON:', content);
    throw new Error('Model did not return valid JSON');
  }

  // Final response back to Forge UI
  return {
    issueKey,
    requirements: parsed.requirements || [],
    test_cases: parsed.test_cases || []
  };
});

// Export resolver for Forge
export const handler = resolver.getDefinitions();
