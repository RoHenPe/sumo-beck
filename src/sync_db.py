import json
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# Resolve .env na raiz (tcc_sumo/)
project_root = Path(__file__).resolve().parents[1]
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

def sync_devices():
    print(">>> 📡 Sync API -> Supabase...")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Credenciais não encontradas no .env")
        return

    manifest_path = project_root / "output" / "api_devices_manifest.json"
    
    if not manifest_path.exists():
        print(f"❌ Manifesto API não encontrado: {manifest_path}")
        return

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

        with open(manifest_path, 'r', encoding='utf-8') as f:
            local_devices = json.load(f)

        print(f"📂 Processando {len(local_devices)} dispositivos...")

        rows = []
        for dev in local_devices:
            if dev.get('type') != 'traffic_control_unit': continue

            rows.append({
                "mac_address": dev['id'],
                "tipo": "SEMAFARO",
                "sumo_id": dev['sumo_id'],
                "status": dev['status'],
                "latitude": dev['geo']['lat'],
                "longitude": dev['geo']['lon'],
                "linked_mac": None
            })

            cam = dev.get('camera')
            if cam:
                rows.append({
                    "mac_address": cam['id'],
                    "tipo": "CAMERA",
                    "sumo_id": None,
                    "status": cam['status'],
                    "latitude": dev['geo']['lat'],
                    "longitude": dev['geo']['lon'],
                    "linked_mac": dev['id']
                })

        if rows:
            supabase.table("dispositivos").upsert(rows, on_conflict="mac_address").execute()
            print(f"✅ SUCESSO! {len(rows)} registros sincronizados.")
        else:
            print("⚠️ Nenhum dispositivo para sincronizar.")

    except Exception as e:
        print(f"❌ ERRO CRÍTICO: {e}")

if __name__ == "__main__":
    sync_devices()