PASSWORD_FORM = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Password required</title>
  <style>
    body { font-family: sans-serif; display: flex; align-items: center; justify-content: center;
           height: 100vh; margin: 0; background: #f4f4f7; color: #1f2937; transition: background 0.3s, color 0.3s; }
    .box { background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); text-align: center; }
    input { padding: 0.6rem; border: 1px solid #ccc; border-radius: 6px; width: 220px; margin-top: 1rem; background: inherit; color: inherit; }
    button { padding: 0.6rem 1.2rem; margin-left: 0.5rem; border: none; border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }
    .error { color: #dc2626; margin-top: 0.75rem; }
    @media (prefers-color-scheme: dark) {
      body { background: #1a1a2e; color: #e0e0e0; }
      .box { background: #16213e; box-shadow: 0 2px 12px rgba(0,0,0,0.3); }
      input { border-color: #4b5563; }
    }
  </style>
</head>
<body>
  <div class="box">
    <h2>🔒 This file is password protected</h2>
    <form method="post">
      <input type="password" name="password" placeholder="Enter password" autofocus>
      <button type="submit">Unlock</button>
    </form>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </div>
</body>
</html>
"""

STATS_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stats — {{ file.original_name }}</title>
  <style>
    body { font-family: sans-serif; background: #f4f4f7; color: #1f2937; margin: 0; padding: 2rem; transition: background 0.3s, color 0.3s; }
    .card { background: #fff; padding: 1.5rem; border-radius: 12px; max-width: 700px; margin: 0 auto 1.5rem; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    h2, h3 { margin-top: 0; }
    table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
    th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #eee; font-size: 0.9rem; word-break: break-all; }
    .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.8rem; }
    .locked { background: #fef3c7; color: #92400e; }
    .public { background: #dcfce7; color: #166534; }
    ul { padding-left: 1.2rem; margin: 0.5rem 0; }
    li { font-size: 0.9rem; margin-bottom: 0.25rem; }
    @media (prefers-color-scheme: dark) {
      body { background: #1a1a2e; color: #e0e0e0; }
      .card { background: #16213e; box-shadow: 0 2px 12px rgba(0,0,0,0.3); }
      th, td { border-bottom-color: #2e3440; }
    }
  </style>
</head>
<body>
  <div class="card">
    <h2>📄 {{ file.original_name }}</h2>
    <span class="badge {{ 'locked' if file.password_hash else 'public' }}">
      {{ '🔒 Protected' if file.password_hash else '🔓 Public' }}
    </span>
    <p>⬇️ <strong>{{ file.downloads }}</strong> total downloads</p>
  </div>

  <div class="card">
    <h3>📊 File Technical Metadata</h3>
    <ul>
      <li><strong>MIME Type:</strong> {{ meta.mime_type }}</li>
      {% if meta.md5 %}<li><strong>MD5 Hash:</strong> <code>{{ meta.md5 }}</code></li>{% endif %}
      {% if meta.sha256 %}<li><strong>SHA256 Hash:</strong> <code>{{ meta.sha256 }}</code></li>{% endif %}
      {% if meta.dimensions %}<li><strong>Dimensions:</strong> {{ meta.dimensions }}</li>{% endif %}
      {% if meta.duration %}<li><strong>Duration:</strong> {{ meta.duration }} seconds</li>{% endif %}
    </ul>
  </div>

  <div class="card">
    <h3>Recent download activity</h3>
    {% if logs %}
    <table>
      <tr><th>Time</th><th>IP</th><th>User Agent</th></tr>
      {% for log in logs %}
      <tr>
        <td>{{ log.readable_time }}</td>
        <td>{{ log.ip }}</td>
        <td>{{ log.user_agent[:60] }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p>No downloads recorded yet.</p>
    {% endif %}
  </div>
</body>
</html>
"""
