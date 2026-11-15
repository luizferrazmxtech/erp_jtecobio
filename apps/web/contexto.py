def exemplos(request):
    """Avisa em todas as telas quando há pedidos/fretes de exemplo no banco."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from apps.integracao.models import RegistroExemplo
    return {"ha_exemplos": RegistroExemplo.existe()}
