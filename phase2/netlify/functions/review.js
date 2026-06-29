// netlify/functions/review.js
// This runs on Netlify's server, never in the user's browser.
// API keys are read from Netlify environment variables, so they are never
// visible in any frontend code or network request the user can see.
//
// Supports multiple AI providers. The frontend sends { prompt, model }
// where model is one of: "claude", "gemini" (more can be added the same
// way — see addProvider notes below).
//
// To keep the frontend simple, every provider's response is normalized to
// the same shape Claude's API returns: { content: [{ type: "text", text }] }

exports.handler = async function (event) {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  let prompt, model;
  try {
    const body = JSON.parse(event.body);
    prompt = body.prompt;
    model = body.model || 'claude';
    if (!prompt || typeof prompt !== 'string') throw new Error('Missing prompt');
  } catch (e) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid request body' }) };
  }

  // Basic size guard so a huge file can't blow up API costs in one call.
  if (prompt.length > 60000) {
    return { statusCode: 400, body: JSON.stringify({ error: 'File too large for AI review (max ~15k lines)' }) };
  }

  try {
    if (model === 'claude') return await callClaude(prompt);
    if (model === 'gemini') return await callGemini(prompt);
    if (model === 'chatgpt') return unavailable('chatgpt', 'OPENAI_API_KEY');
    if (model === 'grok') return unavailable('grok', 'XAI_API_KEY');
    if (model === 'ollama') {
      // Ollama runs on the USER's own machine (http://localhost:11434),
      // which this Netlify function cannot reach (it runs in Netlify's
      // cloud, not the user's browser). So Ollama calls are made directly
      // from the browser in public/index.html (see callOllamaDirect()),
      // never routed through this function. If this branch is ever hit,
      // something called this endpoint with model:"ollama" by mistake.
      return {
        statusCode: 400,
        body: JSON.stringify({ error: 'Ollama requests should be sent directly from the browser to your local Ollama server, not through this Netlify function. See callOllamaDirect() in the frontend.' })
      };
    }
    return { statusCode: 400, body: JSON.stringify({ error: `Unknown model "${model}"` }) };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message || 'Unknown server error' }) };
  }
};

function unavailable(model, envVarName) {
  return {
    statusCode: 501,
    body: JSON.stringify({
      error: `${model} is not configured on this server yet. Add an API key as the "${envVarName}" environment variable in Netlify and wire it up in review.js to enable it.`
    })
  };
}

// ---------- Claude (Anthropic) ----------
async function callClaude(prompt) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'Server is missing ANTHROPIC_API_KEY env var' }) };
  }

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01'
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-6',
      max_tokens: 1200,
      messages: [{ role: 'user', content: prompt }]
    })
  });

  const data = await response.json();
  if (!response.ok) {
    return { statusCode: response.status, body: JSON.stringify({ error: data.error?.message || 'Anthropic API error' }) };
  }

  // Already in the normalized shape — pass through as-is.
  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'claude', content: data.content })
  };
}

// ---------- Gemini (Google) ----------
async function callGemini(prompt) {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'Server is missing GEMINI_API_KEY env var' }) };
  }

  const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`;

  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { maxOutputTokens: 1200 }
    })
  });

  const data = await response.json();
  if (!response.ok) {
    return { statusCode: response.status, body: JSON.stringify({ error: data.error?.message || 'Gemini API error' }) };
  }

  const text = (data.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join('');

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'gemini', content: [{ type: 'text', text }] })
  };
}
