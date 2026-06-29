# AI Chip Studio — ALL 6 PHASES
# Sirf 3 steps mein sab start!

## STEP 1 — API Key + Secret Daalo
.env file kholo (Notepad se) aur apni Gemini key daalo:
    GEMINI_API_KEY=AIzaSyXXXXXXXXXX

Phase 1 ab AICHIP_ENVIRONMENT=production mein chalta hai (docker-compose.yml
mein), is liye ek real JWT secret bhi chahiye -- bina iske container start
hi nahi hoga (security check, see app/config.py). Terminal mein generate
karo aur .env mein paste karo:
    openssl rand -hex 32
    AICHIP_JWT_SECRET_KEY=<wo output yahan paste karo>

## STEP 2 — CMD mein jao
    cd Desktop\AI_Chip_Studio_ALL_IN_ONE

## STEP 3 — Sab start karo!
    docker-compose up -d

## DONE! Ye sab URLs pe jaao:

| Phase | URL | Kya milega |
|-------|-----|-----------|
| Phase 1 | http://localhost:8000/docs | FastAPI Backend |
| Phase 2 | http://localhost:8080 | Verification |
| Phase 3 | http://localhost:5000 | Synthesis Studio |
| Phase 4 | http://localhost:8004/health | Physical Design |
| Phase 5 | http://localhost:8005/health | AI Copilot |
| Phase 6 | http://localhost:3001/health | Cloud API |

## Phase 4 — PDK note

Phase 4 ka Docker build FreePDK45 (open-source, Apache-2.0, free) automatically
clone karta hai build ke time — isliye `docker-compose build` thoda extra
time/disk lega pehli baar. Agar yeh nahi chahiye (sirf sky130/gf180mcu use
karna hai, ya offline build chahiye), `docker-compose.yml` mein
`SKIP_FREEPDK45: "1"` set kar do.

FreePDK45 sirf EDA-research/education ke liye hai — yeh real chip ban
nahi sakta kisi fab se, isliye phase4 ke `/pdks` endpoint pe har PDK ke
saath `is_real_fab` flag milega.

## Band karna ho toh:
    docker-compose down
