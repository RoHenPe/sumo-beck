import os
import json
import sys
from pathlib import Path
from supabase import create_client

# Obtém credenciais do ambiente (passadas pelo script principal)
SB_URL = os.getenv("SUPABASE_URL")
SB_KEY = os.getenv("SUPABASE_KEY")

# Se não vier do ambiente, tenta carregar do .env.local como fallback
if not SB_URL or not SB_KEY:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / "site-web" / ".env.local")
    SB_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    SB_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")

if not SB_URL or not SB_KEY:
    print("ERRO: Credenciais Supabase não encontradas em sync_db.py")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "output" / "api_devices_manifest.json"

def sync():
    print("=== DB SYNC INICIADO ===")
    if not MANIFEST_PATH.exists():
        print(f"ERRO: Manifesto não encontrado: {MANIFEST_PATH}")
        return

    try:
        print(f"Conectando ao Supabase...")
        client = create_client(SB_URL, SB_KEY)
        
        with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
            devices = json.load(f)
            
        print(f"Lendo {len(devices)} dispositivos do manifesto...")
        
        data_to_insert = []
        for d in devices:
            row = {
                "mac_address": d["id"], 
                "tipo": d["type"],
                "latitude": d["geo"]["lat"],
                "longitude": d["geo"]["lon"],
                "status": d.get("status", "active"),
                "sumo_id": d.get("sumo_id"),
                "linked_mac": d.get("linked_to")
            }
            data_to_insert.append(row)

        if data_to_insert:
            print(f"Tentando inserir {len(data_to_insert)} registros...")
            client.table("dispositivos").upsert(data_to_insert, on_conflict="mac_address").execute()
            print("SUCESSO! Dados sincronizados.")
        else:
            print("Nenhum dado para inserir.")

    except Exception as e:
        print(f"ERRO CRÍTICO DURANTE SYNC: {e}")

if __name__ == "__main__":
    sync()