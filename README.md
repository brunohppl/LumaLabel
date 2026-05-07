# LUMA Label Generator — Render Deployment

## API Endpoints

### GET /health
Returns `{ "status": "ok" }` — use this to confirm the service is running.

### POST /generate
Accepts a packing list PDF as base64, returns labels PDF as base64.

**Request body (JSON):**
```json
{
  "pdfBase64": "<base64 encoded PDF>",
  "fileName": "packing_list.pdf"
}
```

**Response (JSON):**
```json
{
  "success": true,
  "pdfBase64": "<base64 encoded labels PDF>",
  "fileName": "LUMA_Labels_STG-2026-089_12MAY.pdf",
  "jobNumber": "STG-2026-089",
  "plNumber": "PL-2026-0412",
  "address": "47 Riverview Terrace, Kangaroo Point QLD 4169",
  "stageDate": "12 May 2026",
  "itemCount": 56,
  "colour": "Red"
}
```

## Deploy to Render

1. Push this folder to a GitHub repository
2. Go to render.com → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Environment:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app`
   - **Plan:** Free
5. Click Deploy

## Make Setup

In your Make scenario, the HTTP module calls:
- **URL:** `https://your-render-url.onrender.com/generate`
- **Method:** POST
- **Body type:** JSON string
- **Body:**
```json
{
  "pdfBase64": "{{base64(2.data)}}",
  "fileName": "{{1.name}}"
}
```

The response `pdfBase64` field contains the labels PDF ready to upload to Slack.
