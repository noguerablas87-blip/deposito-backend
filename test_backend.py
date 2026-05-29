import os
# usar DB temporal limpia
if os.path.exists("deposito.db"):
    os.remove("deposito.db")
os.environ["DATABASE_URL"] = "sqlite:///./deposito.db"

from fastapi.testclient import TestClient
import main

c = TestClient(main.app)

def show(label, r):
    print(f"--- {label} [{r.status_code}]")
    j = r.json()
    if "tools" in j:
        for t in j["tools"]:
            print(f"   {t['id']:8} {t['status']:11} usedBy={t['usedBy']} resBy={t['reservedBy']}")
    else:
        print("   ", j)

# health
print(main.c if False else "")
print("HEALTH:", c.get("/").json())

# crear 3 herramientas (admin)
for tid, name, area in [("CAB-01","Crimpadora RJ45","cableado"),
                         ("CAB-02","Tester de red","cableado"),
                         ("POT-01","Multímetro Fluke","potencia")]:
    c.post("/api/tools", json={"id":tid,"name":name,"area":area})
show("inventario inicial", c.get("/api/state"))

# checkout multiple (Enrique retira CAB-01 y POT-01)
r = c.post("/api/checkout", json={"ids":["CAB-01","POT-01"],"tecnico":"Enrique","area":"cableado","notes":"piso 2"})
show("checkout CAB-01+POT-01 by Enrique", r)
print("   updated:", r.json()["updated"], "skipped:", r.json()["skipped"])

# otro técnico intenta retirar CAB-01 (ya en uso) -> skipped
r = c.post("/api/checkout", json={"ids":["CAB-01"],"tecnico":"Arnaldo","area":"cableado","notes":""})
print("   2do intento CAB-01 skipped:", r.json()["skipped"])

# BUG FIX: reservar MULTIPLES -> solo 1 se reserva, resto ignorado
r = c.post("/api/reserve", json={"ids":["CAB-02"],"tecnico":"Arnaldo","area":"cableado"})
show("Arnaldo reserva CAB-02", r)

# Arnaldo intenta reservar otra teniendo ya una -> 409
r = c.post("/api/reserve", json={"ids":["POT-01"],"tecnico":"Arnaldo","area":"cableado"})
print("2da reserva (debe fallar 409):", r.status_code, r.json().get("detail"))

# usar reserva propia
r = c.post("/api/use-reserve", json={"id":"CAB-02","tecnico":"Arnaldo","area":"cableado"})
show("Arnaldo usa su reserva CAB-02", r)

# devolver CAB-01 (Enrique) -> calcula duracion y cierra log
import time; time.sleep(0.01)
r = c.post("/api/return", json={"id":"CAB-01","tecnico":"Enrique","area":"cableado"})
show("Enrique devuelve CAB-01", r)

# cancel reserve test: reservar y cancelar
c.post("/api/reserve", json={"ids":["CAB-01"],"tecnico":"Rolando","area":"cableado"})
r = c.post("/api/cancel-reserve", json={"id":"CAB-01","tecnico":"Rolando","area":"cableado"})
show("Rolando cancela reserva CAB-01", r)

# logs
logs = c.get("/api/logs").json()
print(f"\nLOGS ({len(logs)} entradas):")
for l in logs[:12]:
    print(f"   {l['action']:8} {l['toolId']:7} {l['tecnico']:9} dur={l['duration']} notes={l['notes']}")

# soft delete
r = c.delete("/api/tools/POT-01")
show("soft-delete POT-01", r)
print("\nTODO OK ✅")
