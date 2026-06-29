# AI Chip Studio — Render pe FREE Live Karne ka Guide

Yeh guide follow karo step-by-step. Kahin bhi error aaye, screenshot/error
message copy karo aur Claude ko bhejo — fix karwa lena.

---

## STEP 1 — GitHub pe code daalo

Render seedha GitHub se deploy karta hai, isliye pehle yeh poora folder
GitHub pe push karna padega.

1. https://github.com pe account banao (agar nahi hai)
2. "New repository" pe click karo — naam do `ai-chip-studio` (ya kuch bhi)
3. **Public** ya **Private** — dono chalega, free tier dono pe kaam karta hai
4. "Create repository" pe click karo
5. Apne computer pe terminal/CMD kholo, jahan yeh `master_project` folder
   extract kiya hai, wahan jao:
   ```
   cd path/to/master_project
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/ai-chip-studio.git
   git push -u origin main
   ```
   (`YOUR_USERNAME` apna GitHub username daal dena, aur repo ka URL
   GitHub khud dikhata hai "Create repository" ke baad — wahi copy karo)

Agar `git` command nahi chal raha, pehle Git install karo:
https://git-scm.com/downloads

---

## STEP 2 — Render account banao

1. https://render.com pe jao, "Get Started" pe click karo
2. **GitHub se sign up karo** (sabse easy — seedha connect ho jayega)
3. Render ko apne GitHub repos access karne do jab pucha jaye

---

## STEP 3 — API Keys taiyar karo (pehle se)

Deploy karne se pehle yeh 2 cheezen ready rakho, kyunki Render beech mein
maangega:

1. **Gemini API key** (free hai) — https://aistudio.google.com/apikey pe
   jao, "Create API Key" pe click karo, copy kar lo
2. **JWT secret** — apne terminal mein yeh chalao aur output copy kar lo:
   ```
   openssl rand -hex 32
   ```
   (Windows pe `openssl` na chale to https://generate-secret.vercel.app/32
   se bhi le sakte ho)

---

## STEP 4 — Render Blueprint se Deploy karo

Yeh sabse important step hai — `render.yaml` file already poora project
mein hai, Render ise automatically padh lega aur saare 6 phases ek saath
bana dega.

1. Render dashboard mein "New +" pe click karo (top right)
2. "Blueprint" choose karo
3. Apna GitHub repo select karo (`ai-chip-studio`)
4. Render `render.yaml` ko dhund kar saari services dikha dega — kuch
   yeh dikhega:
   - `ai-chip-studio-phase1`
   - `ai-chip-studio-phase2-backend`
   - `ai-chip-studio-frontend`
   - `ai-chip-studio-phase3`
   - `ai-chip-studio-phase4`
   - `ai-chip-studio-phase5`
   - `chip-studio-redis`
   - `chip-studio-api` (phase 6)
   - 2 databases
5. "Apply" pe click karo

Render ab saari services banana shuru kar dega. Pehli baar **15-25 minute**
lag sakte hain (sab Docker images build ho rahi hain).

---

## STEP 5 — Secret keys daalo

Build chalte hi, Render dashboard mein har service pe jao aur "Environment"
tab mein yeh values daalo (jahan `sync: false` likha tha render.yaml mein,
wahan Render khud khaali chhod deta hai, manually bharna padta hai):

**`ai-chip-studio-phase1` service mein:**
- `AICHIP_GEMINI_API_KEY` → apni Gemini key paste karo

**`ai-chip-studio-phase3` service mein:**
- `GEMINI_API_KEY` → wahi Gemini key

**`ai-chip-studio-phase5` service mein:**
- `GEMINI_API_KEY` → wahi Gemini key

**`chip-studio-api` (phase6) service mein:**
- `ENCRYPTION_KEY` → ek naya `openssl rand -hex 32` generate karke daalo
- `ANTHROPIC_API_KEY` → agar hai to daalo, nahi to khaali chhod do (phase6
  ka AI copilot feature tab kaam nahi karega, baaki sab chalega)

Har value daalne ke baad "Save Changes" — service apne aap restart ho
jayegi.

---

## STEP 6 — URLs note kar lo

Har service ban jaane ke baad, Render uska URL dikhata hai, jaisa:
`https://ai-chip-studio-phase1.onrender.com`

Yeh **8 URLs** note kar lo (dashboard mein har service ka naam pe click
karke top pe dikh jata hai):
1. `ai-chip-studio-phase1` ka URL
2. `ai-chip-studio-phase2-backend` ka URL
3. `ai-chip-studio-frontend` ka URL ← **yeh aapki asli site hai**
4. `ai-chip-studio-phase3` ka URL
5. `ai-chip-studio-phase4` ka URL
6. `ai-chip-studio-phase5` ka URL
7. `chip-studio-api` ka URL

---

## STEP 7 — Frontend ko backend URLs batao

1. `ai-chip-studio-frontend` ka URL browser mein kholo
2. "Start Designing" ya "Log In" pe click karo
3. Modal khulega — sabse neeche "API Base URL" field mein
   `ai-chip-studio-phase1` ka URL paste karo
4. Account बनाओ / login karo
5. Console page khulega — yahan upar har tab (Synthesis, Physical
   Design, AI Copilot, Cloud API) mein bhi alag "API base" field hai —
   har ek mein uska sahi URL paste karo (Step 6 wali list se)

Yeh ek-baar ka kaam hai — browser save kar lega (localStorage), dobara
nahi karna padega usi browser mein.

---

## STEP 8 — Test karo

1. AI Lint tab mein koi `.v` file upload karo, "Run AI Lint" dabao
2. Result aana chahiye (thoda time lagega — free tier cold-start hota hai)

Agar koi error aaye:
- **CORS error** → phase1's `AICHIP_CORS_ALLOWED_ORIGINS` env var check
  karo, `*` hona chahiye (already render.yaml mein set hai)
- **401/403** → API base URL galat paste hua hoga, dobara check karo
- **Cold start / 30-60 sec delay** → normal hai, free tier 15 min idle
  ke baad so jata hai, pehli request thodi slow hogi

---

## Yaad rakhne wali baatein

- **Free Postgres 30 din mein expire hoti hai** — Render email karega
  warning se pehle, tab "Recreate" karna padega
- **Phase 4 (Physical Design)** asli OpenROAD run nahi kar sakta free
  tier pe (RAM kam hai) — `/health` aur `/pdks` chalenge, par real
  physical-design job fail hoga, yeh expected hai
- **Phase 6 ka login alag hai** — phase1 ke account se connect nahi hai,
  console mein clearly likha hai
- Kabhi bhi kuch tootta hai, Render dashboard → service → "Logs" tab mein
  exact error dikh jata hai — wahi copy karke bhej dena
