# 逆向账本（findings ledger · SQLite 持久化 · 跨会话累积 · 每轮注入上下文）

## ✅ 已确认事实（2）
- ticketparty_aggregator: app Streamlit funcionando (main.py + scrapers/ticketswap.py + canon_orders.json). Verificado con streamlit.testing AppTest (0 excepciones, métricas Eventos=3/Artistas=17/HTTP=200) y servidor real (_stcore/health=200). Build vivo 2836aca4722324e0 con orden canon cacheado.  〔证据:AppTest.from_file(main.py).run() + click Buscar → metrics 200; streamlit run headless → health 200〕
- Entorno: el .venv del repo ticketparty_aggregator está VACÍO (creado por uv, sin pip). httpcloak 1.7.3 vive en /home/juanfran/personal/ticketswap_re/.venv. Para probar: wsl -e bash -lc "cd repo && /home/juanfran/personal/ticketswap_re/.venv/bin/python ...". Streamlit se instaló con pip --target /tmp/stlib (PYTHONPATH=/tmp/stlib + --global.developmentMode false).  〔证据:pip list falla (No module named pip); wheel manylinux de httpcloak en /tmp/hcdl〕

## ⛔ 已否决 · 别重试（0）
（暂无）
