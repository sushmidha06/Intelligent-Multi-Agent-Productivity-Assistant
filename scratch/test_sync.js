import axios from 'axios';
import dotenv from 'dotenv';
dotenv.config();

const BASE_URL = process.env.VITE_API_BASE_URL || 'http://localhost:3001/api';
const CRON_SECRET = process.env.CRON_SHARED_SECRET || 'dev_secret';

async function testSync() {
  console.log('Testing internal project sync...');
  try {
    const r = await axios.post(`${BASE_URL}/internal/projects/sync`, {}, {
      headers: { 'x-cron-secret': CRON_SECRET }
    });
    console.log('Sync result:', r.data);
  } catch (e) {
    console.error('Sync failed:', e.response?.data || e.message);
  }
}

testSync();
