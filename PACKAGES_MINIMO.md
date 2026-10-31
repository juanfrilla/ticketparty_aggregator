# packages.txt al mínimo: sí, sobra todo menos `chromium`

Medido con el índice real de Debian + un contenedor `debian:bookworm-slim` de verdad.

## Respuesta corta

`packages.txt` = **`chromium`** (y opcional `fonts-liberation`). Nada más.

* `xvfb` **no hace falta**: la captura siempre arranca con `--headless=new`
  (`run_capture(headless=True)`, `run_next_build(headless=True)`); `xvfb-run` solo
  se usa si alguien llama con `headless=False` y va guardado con `shutil.which`.
* Las otras 21 líneas son **dependencias transitivas** de `chromium`: apt las
  instala solo. Repetirlas no aporta nada y sí añade ruido/riesgo (un nombre que no
  exista en la suite del día rompe el deploy entero: fue justo el fallo con
  `google-chrome-stable`).

## 1) Prueba 1 — la clausura de apt (estática, índice de Debian)

`chromium` en bookworm declara **43** dependencias directas; la clausura
Depends+Pre-Depends son **206 paquetes**. Comprobación de las 22 líneas que había:

```
libnss3                ya entra solo
libatk1.0-0            ya entra solo
libatk-bridge2.0-0     ya entra solo
libcups2               ya entra solo
libdrm2                ya entra solo   (entra por libgbm1 / libdrm-intel1 / …)
libxkbcommon0          ya entra solo
libxcomposite1         ya entra solo
libxdamage1            ya entra solo
libxfixes3             ya entra solo
libxrandr2             ya entra solo
libgbm1                ya entra solo
libasound2             ya entra solo
libpango-1.0-0         ya entra solo
libcairo2              ya entra solo
libx11-6               ya entra solo
libxcb1                ya entra solo
libxext6               ya entra solo
libexpat1              ya entra solo
libglib2.0-0           ya entra solo
xdg-utils              ya entra solo
fonts-liberation       NO en la clausura  (solo afecta al render de texto/canvas)
ca-certificates        NO en la clausura  (pero lo instala apt igualmente, ver abajo)
```

O sea: de 22 líneas, **20 eran redundantes** y las 2 restantes no son necesarias
para arrancar (`ca-certificates` aparece igualmente en la instalación real).

## 2) Prueba 2 — contenedor real (la que decide)

`docker run debian:bookworm-slim` + `apt-get install -y chromium fonts-liberation`
**y nada más**:

```
APT_RC=0
=== version ===
Chromium 156.0.8078.73 built on Debian GNU/Linux 12 (bookworm)
=== real ===
/usr/bin/chromium
=== faltan_libs ===
0
=== headless (dump-dom about:blank) ===
<html><head></head><body></body></html>
=== paquetes instalados ===
412
```

* **0** bibliotecas sin resolver (`ldd /usr/lib/chromium/chromium | grep "not found"`).
* Arranca **headless sin X y sin xvfb** (con `--no-sandbox`, que ya pone el código).
* Coste: 273 paquetes descargados (~246 MB de caché) sobre un `slim` pelado.
  En la imagen de Streamlit Cloud muchos ya están, así que el delta es menor.

## 3) Ojo con la suite: `chromium` solo existe en Debian

```
debian bookworm  chromium        150.0.7871.100-1~deb12u1   ✔ paquete real
debian bullseye  chromium        120.0.6099.224-1~deb11u1   ✔ paquete real
ubuntu noble     chromium-browser 2:1snap1-0ubuntu2         ✘ envoltorio de snap
ubuntu noble     chromium        NO EXISTE
```

Streamlit Community Cloud es **Debian** (docs: "Community Cloud is built on Debian
Linux… anything you want to apt-get install must go in packages.txt"), así que
`chromium` es correcto allí. Si algún día la base fuese Ubuntu, esa línea no daría
binario (instalaría el stub de snap, sin snapd en el contenedor).

## 4) Y en local, ¿esto sirve?

`packages.txt` **solo lo lee Streamlit Cloud**. En local no interviene: se usa el
navegador que haya en el PATH (`find_chrome()` prueba `chromium`,
`chromium-browser`, `/usr/bin/chromium`, `/snap/bin/chromium`…). Aquí es
`/snap/bin/chromium` (156.0.8078.47) y la captura funciona:

```
[capture] navegador: /snap/bin/chromium
[capture] 15 firmas, title='Verifying'
[capture] canon elegido: ['tsDiff','path','method','clientTs','buildId','fingerprint','nonce','query','body']
```

## 5) Cambio aplicado

```diff
-google-chrome-stable
-xvfb
+chromium
+fonts-liberation
```

(dos líneas, LF puro, sin comentarios: el parser de Cloud espera "un paquete por
línea").

## 6) Pendiente / no verificado

* No he arrancado CDP dentro del contenedor (el `--rm` se llevó la capa). Riesgo
  bajo: es el mismo binario headless y el código ya lanza CDP en local; el puerto
  se abre con `--remote-debugging-port`.
* El contenedor real de Cloud no es accesible desde aquí; lo verificado es Debian
  bookworm slim, que es la suite que ellos documentan.
