"""Ícone na área de notificação do Windows.

O tracker é um servidor local: ele precisa continuar vivo depois que a página
abre. Antes isso era feito por uma janela de console — ela ficava no caminho, e
fechá-la era a única maneira de parar o programa.

Aqui o Flask vai para uma thread e o processo passa a ser representado por um
ícone em *Mostrar ícones ocultos*: clique abre a tela, e o menu tem "Encerrar".
Nenhuma janela de terminal aparece.
"""

import os
import sys
import threading
import webbrowser


def available():
    """pystray e Pillow estão instalados?"""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def _image(assets_dir):
    """O mesmo .ico do atalho; se faltar, desenha um relógio equivalente."""
    from PIL import Image, ImageDraw

    path = os.path.join(assets_dir, "jira-time-tracker.ico")
    if os.path.exists(path):
        try:
            icon = Image.open(path)
            icon.load()  # IcoImageFile já escolhe a maior resolução disponível
            return icon.convert("RGBA")
        except Exception:
            pass

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=14, fill=(99, 91, 255, 255))
    draw.ellipse((14, 14, size - 15, size - 15), outline=(255, 255, 255, 255), width=4)
    draw.line((size // 2, 22, size // 2, size // 2), fill=(255, 255, 255, 255), width=4)
    draw.line((size // 2, size // 2, size - 22, size // 2), fill=(255, 255, 255, 255), width=4)
    return image


def run(app, port, assets_dir, open_browser=True):
    """Sobe o Flask em segundo plano e segura o processo pelo ícone da bandeja.

    Só retorna quando o usuário escolhe "Encerrar" — e aí o processo morre
    junto, levando o servidor com ele.
    """
    import pystray

    url = f"http://127.0.0.1:{port}"

    def serve():
        try:
            app.run(
                host="127.0.0.1",
                port=port,
                debug=False,
                threaded=True,
                use_reloader=False,
            )
        except Exception:
            # Sem console para mostrar o traceback: registra e some com o ícone.
            app.logger.exception("O servidor local parou")
            os._exit(1)

    threading.Thread(target=serve, name="flask", daemon=True).start()

    def do_open(*_args):
        webbrowser.open(url)

    def do_quit(icon, *_args):
        icon.visible = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir o tracker", do_open, default=True),
        pystray.MenuItem(url, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Encerrar", do_quit),
    )

    icon = pystray.Icon(
        "jira-time-tracker",
        _image(assets_dir),
        f"Jira Time Tracker 3 — {url}",
        menu,
    )

    if open_browser:
        threading.Timer(1.2, do_open).start()

    icon.run()  # bloqueia até "Encerrar"

    # O servidor é daemon, mas as threads de requisição em voo podem segurar o
    # processo; aqui já não há nada a salvar.
    sys.stdout.flush() if sys.stdout else None
    os._exit(0)
