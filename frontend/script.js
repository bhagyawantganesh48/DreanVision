const API_BASE      = "http://localhost:3000";   // Express backend (MongoDB history)
const EDGE_API_BASE = "http://localhost:8002";   // Edge FastAPI  (live SQLite + inspect)

const COMPONENT_THRESHOLDS = {
    crankcase:        { label: "Crankcase",        sliderMin:   0, sliderMax:  200, normal_min:  60, normal_max: 120, critical: 140, failure: 150 },
    exhaust_manifold: { label: "Exhaust Manifold", sliderMin: 100, sliderMax:  550, normal_min: 200, normal_max: 400, critical: 450, failure: 500 },
    brake_rotor:      { label: "Brake Rotor",      sliderMin:   0, sliderMax:  400, normal_min:  50, normal_max: 250, critical: 300, failure: 350 },
    cylinder_head:    { label: "Cylinder Head",    sliderMin:   0, sliderMax:  200, normal_min:  80, normal_max: 130, critical: 150, failure: 160 },
    battery_pack:     { label: "Battery Pack",     sliderMin:   0, sliderMax:  100, normal_min:  20, normal_max:  45, critical:  55, failure:  65 }
};

let currentModalUid = null;
let chartInstance = null;

async function fetchStats() {
    try {
        const res  = await fetch(`${EDGE_API_BASE}/dashboard/stats`);
        const data = await res.json();

        const yieldPct = data.total_inspections > 0
            ? (((data.ok_count + data.warning_count) / data.total_inspections) * 100).toFixed(1)
            : "0.0";

        document.getElementById('stat-total').innerText       = data.total_inspections;
        document.getElementById('stat-ok-rate').innerText     = `${yieldPct}%`;
        document.getElementById('stat-defect-rate').innerText = `${data.defect_rate_percent}%`;
        document.getElementById('stat-warning').innerText     = data.warning_count;

        updateChart(data);
    } catch (e) { console.error("Stats fetching failed", e); }
}

async function fetchFeed(query = "") {
    try {
        const url = query
            ? `${EDGE_API_BASE}/dashboard/inspections?search=${encodeURIComponent(query)}`
            : `${EDGE_API_BASE}/dashboard/inspections`;
        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('tableBody');
        tbody.innerHTML = "";

        data.forEach(row => {
            let statusColor = row.status === 'OK' ? '#4caf50' : (row.status === 'WARNING' ? '#fb8c00' : '#ef5350');

            let tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${row.part_uid}</td>
                <td>${row.component_name}</td>
                <td>${row.temperature.toFixed(1)}</td>
                <td style="color: ${statusColor}; font-weight: bold;">${row.status}</td>
                <td>${row.verified_status}</td>
                <td>${row.timestamp}</td>
                <td><button onclick="openModal('${row.part_uid}')">Details</button></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) { console.error("Feed fetch failed", e); }
}

function searchInspections() {
    const q = document.getElementById('searchInput').value;
    fetchFeed(q);
}

function resetSearch() {
    document.getElementById('searchInput').value = "";
    fetchFeed();
}

async function openModal(uid) {
    try {
        const res = await fetch(`${EDGE_API_BASE}/dashboard/inspection/${uid}`);
        const data = await res.json();
        currentModalUid = uid;

        document.getElementById('modal-uid').innerText = data.part_uid;
        document.getElementById('modal-component').innerText = data.component_name;
        document.getElementById('modal-temp').innerText = data.temperature;
        document.getElementById('modal-status').innerText = data.status;
        document.getElementById('modal-status').style.color = data.status === 'OK' ? '#4caf50' : '#ef5350';
        document.getElementById('modal-time').innerText = data.timestamp;
        document.getElementById('modal-verified').innerText = data.verified_status;

        // Correctly route the image mapping using server static mount
        document.getElementById('modal-img').src = `${API_BASE}/${data.image_path}`;

        document.getElementById('detailModal').style.display = 'block';
    } catch (e) {
        alert("Failed to load details");
    }
}

function closeModal() {
    document.getElementById('detailModal').style.display = 'none';
    currentModalUid = null;
}

async function submitVerification() {
    if (!currentModalUid) return;

    const status = document.getElementById('verify-select').value;
    const user = document.getElementById('logged-user').innerText;

    try {
        const res = await fetch(`${EDGE_API_BASE}/dashboard/verify/${currentModalUid}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ verified_status: status, verified_by: user })
        });

        if (res.ok) {
            alert("Verification Saved successfully");
            closeModal();
            fetchFeed();
        } else {
            alert("Failed to save verification.");
        }
    } catch (e) {
        alert("Network Error during verification");
    }
}

function updateChart(data) {
    const ctx = document.getElementById('defectChart').getContext('2d');

    if (chartInstance) { chartInstance.destroy(); }

    chartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['OK', 'WARNING', 'NOK'],
            datasets: [{
                data: [data.ok_count, data.warning_count, data.nok_count],
                backgroundColor: ['#4caf50', '#fb8c00', '#ef5350'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { color: "#ffffff" } }
            }
        }
    });
}


// (Removed WebSocket dependency since we are polling every 5s per user request)

// ── Initial load & auto-refresh ───────────────────────────────────────────────
fetchStats();
fetchFeed();

setInterval(() => {
    const si = document.getElementById('searchInput');
    if ((!si || !si.value) && !currentModalUid) {
        fetchStats();
        fetchFeed();
    }
}, 5000);
