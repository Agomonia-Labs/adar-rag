# Frontend Deployment + DNS Setup
## Firebase Hosting + Route 53 → docintel.adar.agomoniai.com

---

## Part 1 — Prerequisites (run once)

```bash
# Install Firebase CLI globally
npm install -g firebase-tools

# Login to Firebase
firebase login
# Opens browser → sign in with your Google account (bkd108@gmail.com)

# Verify login
firebase projects:list
# Should show bdas-493785 in the list
```

---

## Part 2 — Create Firebase Hosting site (run once)

```bash
# Create the hosting site
firebase hosting:sites:create docintel-adar \
  --project=bdas-493785

# Verify it was created
firebase hosting:sites:list --project=bdas-493785
```

---

## Part 3 — Verify firebase.json and .firebaserc

Make sure these two files exist in your project root:

**firebase.json** — should look like:
```json
{
  "hosting": {
    "site":   "docintel-adar",
    "public": "frontend/dist",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      {
        "source": "/api/**",
        "run": {
          "serviceId": "docintel-backend",
          "region":    "us-central1"
        }
      },
      {
        "source": "**",
        "destination": "/index.html"
      }
    ]
  }
}
```

**.firebaserc** — should look like:
```json
{
  "projects": {
    "default": "bdas-493785"
  }
}
```

---

## Part 4 — Build and deploy frontend

```bash
# Step 1 — install npm dependencies
cd frontend
npm ci

# Step 2 — build with Vite
npm run build
# Output goes to frontend/dist/

# Step 3 — go back to project root
cd ..

# Step 4 — deploy to Firebase Hosting
firebase deploy --only hosting --project=bdas-493785
```

You will see:
```
=== Deploying to 'bdas-493785'...
i  deploying hosting
✔  hosting[docintel-adar]: file upload complete
✔  hosting[docintel-adar]: version finalized
✔  hosting[docintel-adar]: release complete

Hosting URL: https://docintel-adar.web.app
```

Test it works (before custom domain):
```bash
curl https://docintel-adar.web.app/api/health
# Should return: {"status":"ok","llm":"gemini","db_connected":true}
```

---

## Part 5 — Add custom domain in Firebase Console

1. Go to **https://console.firebase.google.com**
2. Select project **bdas-493785**
3. Left menu → **Build → Hosting**
4. Click **Add custom domain**
5. Enter: `docintel.adar.agomoniai.com`
6. Click **Continue**

Firebase will show you **two A records** like:
```
Type    Name              Value
A       docintel.adar     151.101.1.195
A       docintel.adar     151.101.65.195
```

**Keep this browser tab open** — you need these values for Route 53.

---

## Part 6 — Add DNS records in Route 53

1. Go to **https://console.aws.amazon.com/route53**
2. Click **Hosted zones**
3. Click **agomoniai.com**
4. Click **Create record**

Add the first A record:
```
Record name:  docintel.adar
Record type:  A
Value:        151.101.1.195   ← paste the IP Firebase gave you
TTL:          300
```
Click **Add another record**, add the second IP:
```
Record name:  docintel.adar
Record type:  A
Value:        151.101.65.195  ← second IP from Firebase
TTL:          300
```
Click **Create records**

**Or via AWS CLI:**
```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id YOUR_ZONE_ID \
  --change-batch '{
    "Changes": [
      {
        "Action": "CREATE",
        "ResourceRecordSet": {
          "Name": "docintel.adar.agomoniai.com",
          "Type": "A",
          "TTL": 300,
          "ResourceRecords": [
            {"Value": "151.101.1.195"},
            {"Value": "151.101.65.195"}
          ]
        }
      }
    ]
  }'
```

Get your hosted zone ID:
```bash
aws route53 list-hosted-zones \
  --query "HostedZones[?Name=='agomoniai.com.'].Id" \
  --output text
# Returns something like: /hostedzone/Z1234ABCDEFGH
```

---

## Part 7 — Verify DNS propagation

```bash
# Check DNS is resolving (takes 1-10 minutes)
nslookup docintel.adar.agomoniai.com
# Should return the Firebase IP addresses

# Or use dig
dig docintel.adar.agomoniai.com
```

Go back to Firebase Console → Hosting → your custom domain.
Firebase automatically verifies the DNS and provisions an SSL certificate.
This takes **5-15 minutes**.

Status changes:
```
Needs setup → Pending → Connected ✓
```

---

## Part 8 — Verify full deployment

Once Firebase shows **Connected**:

```bash
# Test HTTPS
curl https://docintel.adar.agomoniai.com/api/health

# Expected response:
# {"status":"ok","llm":"gemini","db_connected":true}

# Open in browser
open https://docintel.adar.agomoniai.com
```

---

## Future deploys — just run:

```bash
# Both frontend and backend
bash deploy.sh

# Frontend only (when only React code changed)
bash deploy.sh --frontend

# Backend only (when only Python code changed)
bash deploy.sh --backend
```

---

## Troubleshooting

**Firebase deploy fails — not logged in:**
```bash
firebase login --reauth
```

**Firebase deploy fails — wrong project:**
```bash
firebase use bdas-493785
firebase deploy --only hosting
```

**DNS not propagating after 30 minutes:**
```bash
# Check from multiple DNS servers
dig docintel.adar.agomoniai.com @8.8.8.8    # Google DNS
dig docintel.adar.agomoniai.com @1.1.1.1    # Cloudflare DNS
```

**SSL certificate pending for too long:**
- Firebase needs port 80 accessible to verify the domain
- Check that no firewall is blocking port 80 on the IPs Firebase gave you
- In Firebase Console → Hosting → custom domain → click **Verify** again

**API calls returning 404 after deploy:**
- The Cloud Run rewrite in `firebase.json` might not be applied
- Check the rewrite is configured:
```bash
firebase hosting:sites:describe docintel-adar --project=bdas-493785
```
- Redeploy: `firebase deploy --only hosting --project=bdas-493785`

**Cloud Run not receiving /api requests:**
- Verify `--allow-unauthenticated` was set on the Cloud Run service:
```bash
gcloud run services describe docintel-backend \
  --region=us-central1 \
  --project=bdas-493785 \
  --format="value(spec.template.spec.serviceAccountName)"
```