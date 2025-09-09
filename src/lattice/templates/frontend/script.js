(() => {
  const API_BASE = (localStorage.getItem('API_BASE') || 'http://localhost:8000').replace(/\/$/, '');
  const API_KEY = localStorage.getItem('API_KEY') || '';

  const $items = document.getElementById('items');
  const $form = document.getElementById('create-form');
  const $status = document.getElementById('status');

  const h = () => ({
    'Content-Type': 'application/json',
    ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
  });

  async function loadItems() {
    try {
      const res = await fetch(`${API_BASE}/items`);
      if (!res.ok) throw new Error(`GET /items -> ${res.status}`);
      const data = await res.json();
      $items.innerHTML = '';
      for (const it of data) {
        const li = document.createElement('li');
        li.textContent = `${it.name} — ${it.description || ''}`;
        $items.appendChild(li);
      }
    } catch (e) {
      console.error(e);
      $items.innerHTML = `<li>Error loading items: ${e}</li>`;
    }
  }

  async function createItem(name, description) {
    const res = await fetch(`${API_BASE}/items`, {
      method: 'POST',
      headers: h(),
      body: JSON.stringify({ name, description }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`POST /items -> ${res.status} ${text}`);
    }
    return res.json();
  }

  if ($form) {
    $form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData($form);
      const name = String(fd.get('name') || '');
      const description = String(fd.get('description') || '');
      $status.textContent = 'Creating...';
      try {
        await createItem(name, description);
        $status.textContent = 'Created!';
        $form.reset();
        await loadItems();
      } catch (e) {
        console.error(e);
        $status.textContent = `Error: ${e}`;
      }
    });
  }

  loadItems();
})();