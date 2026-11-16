def exemplos(request):
    """Avisa em todas as telas quando há pedidos/fretes de exemplo no banco."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from apps.integracao.models import RegistroExemplo
    return {"ha_exemplos": RegistroExemplo.existe()}


_VERSAO = None


def versao_static(request):
    """Número que muda quando um CSS/JS muda (?v=...), para o navegador não
    usar a versão velha guardada em cache depois de atualizar o sistema."""
    global _VERSAO
    if _VERSAO is None:
        from pathlib import Path

        from django.conf import settings

        mtimes = [p.stat().st_mtime for pasta in settings.STATICFILES_DIRS
                  for p in Path(pasta).rglob("*") if p.suffix in (".css", ".js")]
        _VERSAO = str(int(max(mtimes, default=0)))
    return {"versao_static": _VERSAO}
