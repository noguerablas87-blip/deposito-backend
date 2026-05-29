# Control de Herramientas — Backend central + apps de área

Migración de `localStorage` (aislado por tablet) a un **backend compartido**, para
que las 3 tablets (Cableado / Potencia / Refrigeración) y la App Principal vean el
mismo inventario en tiempo real.

```
deposito-backend/        ← API (FastAPI) → desplegar en Railway
  main.py
  requirements.txt
  Procfile
app-cableado.html        ← tablet Cableado
app-potencia.html        ← tablet Potencia
app-refrigeracion.html   ← tablet Refrigeración
app-principal.html       ← App Principal (admin)
```

---

## 1. Desplegar el backend en Railway

1. Subí la carpeta `deposito-backend/` a un repo de GitHub.
2. En tu proyecto de Railway: **New → GitHub Repo** y elegí ese repo. Queda como
   un **servicio nuevo** (independiente de vozviaje / restaurant-backend).
3. Conectá la base de datos. Tenés dos opciones:

   **A) Reusar el Postgres que ya tenés** (el de vozviaje, mismo proyecto):
   en el servicio nuevo → *Variables* → *New Variable* → *Add Reference* →
   elegí el servicio **Postgres** → `DATABASE_URL`. Queda como
   `DATABASE_URL = ${{Postgres.DATABASE_URL}}`.
   Mis tablas se llaman `deposito_tools` y `deposito_logs`, así que conviven con
   las de vozviaje sin pisarlas. *(El que vozviaje use asyncpg no afecta: cada
   servicio tiene sus propias dependencias; este backend usa psycopg2.)*

   **B) Base dedicada:** agregá un plugin **PostgreSQL** nuevo; Railway inyecta
   `DATABASE_URL` solo. Aislamiento total.

4. (Opcional) En *Variables* agregá `ADMIN_TOKEN` con un valor secreto para proteger
   los endpoints de admin (crear/editar/borrar herramientas y ver/borrar logs).
5. Railway expone una URL tipo `https://deposito-production.up.railway.app`. Abrila:
   deberías ver `{"ok": true, "service": "deposito", ...}`.

> Prueba local: `pip install -r requirements.txt` y `uvicorn main:app --reload`.
> Sin `DATABASE_URL` usa SQLite (`deposito.db`) — ideal para probar antes de subir.

---

## 2. Conectar las 3 apps

En cada `app-*.html`, arriba del `<script>`, cambiá **una sola línea**:

```js
const API_BASE = 'https://xxxx.up.railway.app';  // ← URL de tu backend
```

Subí cada archivo a su tablet (o a Netlify/Vercel como ya venías haciendo). Cada
tablet abre solo el archivo de su área; ahora todas comparten el inventario.

Un punto en el header muestra el estado de conexión: **verde** = conectado,
**rojo** = sin conexión (reintenta solo cada 4 s y conserva lo último en pantalla).

---

## 3. App Principal (admin)

`app-principal.html` ya está migrada. Igual que las apps de área, cambiá su
`API_BASE`. Además, si configuraste `ADMIN_TOKEN` en el backend, ponelo en la
constante `ADMIN_TOKEN` de arriba del `<script>` (si no, dejala vacía).

Endpoints de admin (requieren header `X-Admin-Token` solo si configuraste `ADMIN_TOKEN`):

| Acción            | Método | Ruta                       | Body                       |
|-------------------|--------|----------------------------|----------------------------|
| Crear herramienta | POST   | `/api/tools`               | `{id, name, area, image?}` |
| Editar            | PUT    | `/api/tools/{id}`          | `{name?, area?, image?}`   |
| Borrar (soft)     | DELETE | `/api/tools/{id}`          | —                          |
| Ver bitácora      | GET    | `/api/logs?limit=500`      | —                          |
| Borrar historial  | DELETE | `/api/logs?area=cableado`  | — (sin `area` borra todo)  |

Endpoints de técnico (los usan las apps de área, sin token):
`/api/state` (GET), `/api/checkout`, `/api/reserve`, `/api/use-reserve`,
`/api/return`, `/api/cancel-reserve` (POST).

---

## 4. Migrar el inventario existente (una sola vez)

Si una tablet ya tiene herramientas cargadas en su `localStorage`, abrí esa tablet,
presioná **F12 → Console** y pegá esto (cambiando la URL y, si aplica, el token):

```js
(async () => {
  const API = 'https://xxxx.up.railway.app';
  const TOKEN = '';  // poné tu ADMIN_TOKEN si lo configuraste, si no dejá ''
  const d = JSON.parse(localStorage.getItem('deposito_v3') || '{"tools":[],"log":[]}');
  const r = await fetch(API + '/api/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(TOKEN ? {'X-Admin-Token': TOKEN} : {}) },
    body: JSON.stringify({ tools: d.tools, logs: d.log, overwrite: true })
  });
  console.log(await r.json());
})();
```

---

## Qué cambió respecto a la versión anterior

- **Datos compartidos** entre tablets (antes cada `localStorage` quedaba aislado).
- **Lógica en el servidor**: retiro/reserva/devolución son atómicos → no hay choques
  si dos tablets tocan la misma herramienta a la vez.
- **Bug de reservas corregido**: antes, al reservar varias seleccionadas, solo se
  reservaba 1 y las demás se descartaban en silencio. Ahora se respeta el límite de
  1 reserva activa por técnico y se avisa explícitamente.
- **Indicador de conexión** en el header.
- Lógica idéntica entre las 3 apps (solo cambian tema, título y lista de técnicos).
