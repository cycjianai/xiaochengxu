let logTimer = null;
let searchTimer = null;
let lastLogCount = 0;

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('product-tbody')) {
        loadUserInfo();
        loadProducts();
        refreshProxyStatus();
        setInterval(refreshProxyStatus, 4000);  // 代理状态每 4s 实时刷新
        startLogPolling();
        refreshHealth(false);
        setInterval(() => refreshHealth(false), 60000);
        // 商品表格每 3s 自动刷新（抓到的新品自动出现，无需手动刷新）
        setInterval(() => {
            const q = document.getElementById('search-input');
            if (q && document.activeElement === q) return;  // 用户在搜索框打字时跳过
            loadProducts();
        }, 3000);
    }
});

async function refreshHealth(showDetail) {
    const chip = document.getElementById('health-chip');
    if (!chip) return;
    try {
        const res = await fetch('/api/health/anti-detection');
        if (!res.ok) throw new Error('http ' + res.status);
        const data = await res.json();
        const issues = [];
        if (!data.cert_trusted) issues.push('证书未信任');
        if (data.wintun_ready === false) issues.push('WinTUN 不可用');
        if (Array.isArray(data.conflicting_tools) && data.conflicting_tools.length > 0) {
            issues.push('冲突工具: ' + data.conflicting_tools.join(','));
        }
        chip.classList.remove('status-chip-warn', 'status-chip-ok');
        if (issues.length === 0) {
            chip.textContent = '环境健康';
            chip.classList.add('status-chip-ok');
        } else {
            chip.textContent = '风险 ' + issues.length;
            chip.classList.add('status-chip-warn');
        }
        chip.title = data.recommendations || '';
        if (showDetail) {
            const parts = [
                '证书已信任：' + (data.cert_trusted ? '是' : '否'),
                '管理员/root：' + (data.is_elevated ? '是' : '否'),
            ];
            if (data.wintun_ready !== undefined) {
                parts.push('WinTUN：' + (data.wintun_ready ? '可用' : '不可用'));
            }
            parts.push('冲突工具：' + ((data.conflicting_tools || []).join(',') || '无'));
            parts.push('建议：' + (data.recommendations || ''));
            alert(parts.join('\n'));
        }
    } catch (err) {
        chip.textContent = '健康未知';
        chip.title = '获取健康状态失败: ' + (err.message || err);
    }
}

// Auth removed — no-op stubs kept so existing call sites don't break.
async function loadUserInfo() {}
async function doLogout() {}

async function refreshProxyStatus() {
    let data;
    try {
        const res = await fetch('/api/proxy/status');
        data = await res.json();
    } catch {
        data = { running: false, message: '无法连接本地服务' };
    }
    const running = !!data.running;
    const msg = data.message || '';
    const chip = document.getElementById('proxy-chip');
    if (chip) {
        chip.classList.remove('status-chip-ok', 'status-chip-warn');
        chip.classList.add(running ? 'status-chip-ok' : 'status-chip-warn');
        chip.textContent = running ? '代理：抓取中' : '代理：未运行';
        chip.title = msg;
    }
    const txt = document.getElementById('proxy-status-text');
    if (txt) txt.textContent = `代理状态：${running ? '抓取中' : '未运行'}${msg ? ' / ' + msg : ''}`;
}

async function loadProducts(q) {
    const url = q ? `/api/products?q=${encodeURIComponent(q)}` : '/api/products';
    const res = await fetch(url);
    if (res.status === 401) {
        window.location.href = '/login';
        return;
    }
    const products = await res.json();
    renderProducts(products);
}

function renderProducts(products) {
    const tbody = document.getElementById('product-tbody');
    if (!products.length) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--text-secondary);padding:24px">暂无数据</td></tr>';
        return;
    }
    tbody.innerHTML = products.map(p => {
        const pics = Array.isArray(p.product_pic_list) ? p.product_pic_list : [];
        const imgHtml = pics.length
            ? pics.map(url => `<div class="thumb-wrap"><img class="thumb" src="${esc(url)}" alt=""><img class="thumb-zoom" src="${esc(url)}" alt=""></div>`).join('')
            : '<span style="color:var(--text-secondary)">-</span>';
        return `
        <tr>
            <td>${esc(p.sku_id)}</td>
            <td>${esc(p.poi_name)}</td>
            <td>${esc(p.upc)}</td>
            <td>${esc(p.product_name)}</td>
            <td>${imgHtml}</td>
            <td>${esc(p.spec)}</td>
            <td>${num(p.origin_price)}</td>
            <td>${num(p.price)}</td>
            <td>${(p.monthly_sales === null || p.monthly_sales === undefined) ? '-' : num(p.monthly_sales)}</td>
            <td>${esc(p.synced_at || '-')}</td>
            <td class="actions">
                <button class="btn btn-sm btn-secondary" onclick="showEditModal(${p.id})">编辑</button>
                <button class="btn btn-sm btn-danger" onclick="deleteProduct(${p.id})">删除</button>
                <button class="btn btn-sm btn-secondary" onclick="viewJson(${p.id})">JSON</button>
            </td>
        </tr>`;
    }).join('');
}

function num(value) {
    return value == null ? '' : value;
}

function searchProducts() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        const q = document.getElementById('search-input').value.trim();
        loadProducts(q || undefined);
    }, 250);
}

function showAddModal() {
    document.getElementById('modal-title').textContent = '新增商品';
    document.getElementById('product-form').reset();
    document.getElementById('form-id').value = '';
    document.getElementById('form-source-platform').value = 'wechat_meituan';
    document.getElementById('form-product-pic').value = '[]';
    document.getElementById('form-raw-json').value = '';
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function showEditModal(id) {
    const res = await fetch('/api/products');
    const products = await res.json();
    const p = products.find(x => x.id === id);
    if (!p) return;
    document.getElementById('modal-title').textContent = '编辑商品';
    document.getElementById('form-id').value = p.id;
    document.getElementById('form-source-platform').value = p.source_platform || 'wechat_meituan';
    document.getElementById('form-sku-id').value = p.sku_id;
    document.getElementById('form-poi-name').value = p.poi_name;
    document.getElementById('form-product-name').value = p.product_name;
    document.getElementById('form-upc').value = p.upc;
    document.getElementById('form-spec').value = p.spec;
    document.getElementById('form-origin-price').value = p.origin_price;
    document.getElementById('form-price').value = p.price;
    document.getElementById('form-stock').value = p.stock;
    document.getElementById('form-product-pic').value = JSON.stringify(p.product_pic_list || [], null, 2);
    document.getElementById('form-raw-json').value = p.raw_json || '';
    document.getElementById('modal-overlay').style.display = 'flex';
}

async function submitProduct(e) {
    e.preventDefault();
    try {
        const id = document.getElementById('form-id').value;
        const data = {
            source_platform: document.getElementById('form-source-platform').value.trim() || 'wechat_meituan',
            sku_id: document.getElementById('form-sku-id').value.trim(),
            poi_name: document.getElementById('form-poi-name').value.trim(),
            product_name: document.getElementById('form-product-name').value.trim(),
            upc: document.getElementById('form-upc').value.trim(),
            spec: document.getElementById('form-spec').value.trim(),
            origin_price: parseFloat(document.getElementById('form-origin-price').value) || 0,
            price: parseFloat(document.getElementById('form-price').value) || 0,
            stock: parseInt(document.getElementById('form-stock').value, 10) || 0,
            product_pic: safeJson(document.getElementById('form-product-pic').value, []),
            raw_json: safeJson(document.getElementById('form-raw-json').value, null),
        };
        const url = id ? `/api/products/${id}` : '/api/products';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(body.error || '保存失败');
            return;
        }
        hideModal();
        loadProducts();
    } catch (err) {
        alert(err.message || '保存失败');
    }
}

async function deleteProduct(id) {
    if (!confirm('确认删除该商品？')) return;
    await fetch(`/api/products/${id}`, { method: 'DELETE' });
    loadProducts();
}

async function viewJson(id) {
    const res = await fetch(`/api/products/${id}/json`);
    const data = await res.json();
    document.getElementById('json-content').textContent = JSON.stringify(data, null, 2);
    document.getElementById('json-overlay').style.display = 'flex';
}

function hideModal() {
    document.getElementById('modal-overlay').style.display = 'none';
}

function closeModal(e) {
    if (e.target === document.getElementById('modal-overlay')) hideModal();
}

function hideJsonModal() {
    document.getElementById('json-overlay').style.display = 'none';
}

function closeJsonModal(e) {
    if (e.target === document.getElementById('json-overlay')) hideJsonModal();
}

function copyJson() {
    const text = document.getElementById('json-content').textContent;
    navigator.clipboard.writeText(text);
}

function startLogPolling() {
    fetchLogs();
    logTimer = setInterval(fetchLogs, 2000);
}

async function fetchLogs() {
    const res = await fetch('/api/logs');
    const logs = await res.json();
    const panel = document.getElementById('log-panel');
    document.getElementById('log-count').textContent = `${logs.length} 条`;
    panel.innerHTML = logs.map(l =>
        `<div class="log-line ${l.level}">[${esc(l.time)}] [${esc(l.level)}] ${esc(l.message)}</div>`
    ).join('');
    panel.scrollTop = panel.scrollHeight;
    if (logs.length !== lastLogCount) {
        lastLogCount = logs.length;
    }
}

function pushClientLog(level, message) {
    console.log(`[${level}] ${message}`);
}

function safeJson(text, fallback) {
    const value = (text || '').trim();
    if (!value) return fallback;
    try {
        return JSON.parse(value);
    } catch {
        throw new Error('JSON 格式不正确');
    }
}

function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}
