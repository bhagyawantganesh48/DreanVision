require('dotenv').config();
const express = require('express');
const { MongoClient } = require('mongodb');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json());
// Serve static thermal images saved by the Python script
app.use(express.static(path.join(__dirname, '../data/snapshots')));

// MongoDB Configuration
const uri = process.env.MONGO_URI || "mongodb+srv://harsh1:%23london%261234@harsh1.hfifgiu.mongodb.net/";
const client = new MongoClient(uri);

let db, collection;
let clients = [];

// Connect to MongoDB
async function connectDB() {
    try {
        await client.connect();
        db = client.db("dreamvision");
        collection = db.collection("inspections");
        console.log("✅ Connected to MongoDB Atlas");
    } catch (error) {
        console.error("❌ MongoDB connection error:", error);
    }
}
connectDB();

// ── API ROUTES ──────────────────────────────────────────────────────────────

// SSE Endpoint for instant frontend updates
app.get('/stream', (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.write(`data: {"connected": true}\n\n`);
    clients.push(res);
    req.on('close', () => { clients = clients.filter(c => c !== res); });
});

// Health Check
app.get('/', (req, res) => {
    res.json({ status: "API is running", timestamp: new Date() });
});

/**
 * 1. POST /inspection
 * Receive inspection data from the ESP32 (or Python edge router)
 */
app.post('/inspection', async (req, res) => {
    try {
        const data = req.body;
        
        // Basic validation
        if (!data.part_uid || data.temperature === undefined) {
             return res.status(400).json({ error: "Missing required fields" });
        }

        // DASHBOARD AUTO-ASSIGN STATUS RULE
        let temp = Number(data.temperature);
        let calculatedStatus = 'UNKNOWN';
        if (temp <= 80) {
            calculatedStatus = 'OK';
        } else if (temp <= 120) {
            calculatedStatus = 'WARNING';
        } else {
            calculatedStatus = 'NOK';
        }

        // Default missing values if sent by raw ESP32
        const record = {
            part_uid: data.part_uid,
            component_name: data.component_name || "Unknown",
            temperature: temp,
            status: calculatedStatus, // "OK", "WARNING", "NOK"
            device_id: data.device_id || "ESP32_THERMAL_01",
            timestamp: data.timestamp || new Date().toISOString(),
            verified_status: data.verified_status || "Pending",
            image_path: data.image_path || null
        };

        // Upsert to avoid duplicate UUIDs if retried
        await collection.updateOne(
            { part_uid: record.part_uid },
            { $set: record },
            { upsert: true }
        );

        // Broadcast instant update to all connected Dashboards
        clients.forEach(c => c.write(`data: ${JSON.stringify({ event: 'new_inspection', record })}\n\n`));

        res.status(201).json({ message: "Inspection recorded successfully", record });
    } catch (error) {
        console.error("Error inserting inspection:", error);
        res.status(500).json({ error: "Database error" });
    }
});

/**
 * 2. GET /results
 * Return the latest 50 inspection results for the dashboard table
 */
app.get('/results', async (req, res) => {
    try {
        // Fetch the 50 newest recordings descending by timestamp
        const results = await collection.find()
            .sort({ timestamp: -1 })
            .limit(50)
            .toArray();
            
        res.json(results);
    } catch (error) {
        console.error("Error fetching results:", error);
        res.status(500).json({ error: "Database error" });
    }
});

/**
 * 3. GET /dashboard/inspection/:uid
 * Fetch details of a single inspection for the modal
 */
app.get('/dashboard/inspection/:uid', async (req, res) => {
    try {
        const uid = req.params.uid;
        const result = await collection.findOne({ part_uid: uid });
        if (!result) return res.status(404).json({ error: "Inspection not found" });
        res.json(result);
    } catch (error) {
        console.error("Error fetching inspection:", error);
        res.status(500).json({ error: "Database error" });
    }
});

/**
 * 4. POST /dashboard/verify/:uid
 * Save supervisor verification override
 */
app.post('/dashboard/verify/:uid', async (req, res) => {
    try {
        const uid = req.params.uid;
        const { verified_status, verified_by } = req.body;
        
        const result = await collection.updateOne(
            { part_uid: uid },
            { $set: { verified_status, verified_by } }
        );
        
        if (result.matchedCount === 0) {
            return res.status(404).json({ error: "Inspection not found" });
        }
        res.json({ message: "Verification saved successfully" });
    } catch (error) {
        console.error("Error verifying inspection:", error);
        res.status(500).json({ error: "Database error" });
    }
});

/**
 * 5. GET /stats
 * Return aggregated statistics: total, OK count, NOK count, yield %
 */
app.get('/stats', async (req, res) => {
    try {
        // Run aggregations in parallel
        const [totalCount, okCount, warningCount, nokCount] = await Promise.all([
            collection.countDocuments(),
            collection.countDocuments({ status: "OK" }),
            collection.countDocuments({ status: "WARNING" }),
            collection.countDocuments({ status: "NOK" })
        ]);

        let yieldPercentage = 0;
        if (totalCount > 0) {
            // Yield = (OK + WARNING) / Total
            // Note: Adjust math if WARNING shouldn't be considered "yield"
            const passCount = okCount + warningCount; 
            yieldPercentage = ((passCount / totalCount) * 100).toFixed(1);
        }

        let defectPercentage = 0;
        if(totalCount > 0) {
             defectPercentage = ((nokCount / totalCount) * 100).toFixed(1);
        }

        res.json({
            total_inspections: totalCount,
            ok_count: okCount,
            warning_count: warningCount,
            nok_count: nokCount,
            yield_percent: yieldPercentage,
            defect_percent: defectPercentage
        });
    } catch (error) {
        console.error("Error fetching stats:", error);
        res.status(500).json({ error: "Database error" });
    }
});

// Start Server
app.listen(PORT, () => {
    console.log(`🚀 DreamVision Express API running on port ${PORT}`);
});
