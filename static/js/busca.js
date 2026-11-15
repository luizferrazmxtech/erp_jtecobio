/* Busca digitável (apps/web/busca.py).

   Cada campo .busca-campo tem uma caixa de texto e, escondida, a lista
   original (<select>) que o formulário envia. Você digita, o sistema traz até
   15 cadastros que combinam; ao escolher um, ele vai para a lista escondida e
   dispara "change" — quem depende do campo (ex.: contratos do cliente) atualiza.
   Setas ↑ ↓ andam na lista, Enter escolhe, Esc fecha. */
(function () {
  function iniciar(caixa) {
    var texto = caixa.querySelector(".busca-texto");
    var escolha = caixa.querySelector(".busca-escolha");
    var lista = caixa.querySelector(".busca-lista");
    var url = caixa.dataset.url, timer = null, itens = [], ativo = -1, pedido = 0;

    var atual = escolha.options[escolha.selectedIndex];
    if (atual && atual.value) texto.value = atual.textContent.trim();

    function fechar() { lista.hidden = true; ativo = -1; }
    function marcar(i) {
      ativo = i;
      Array.prototype.forEach.call(lista.children, function (el, j) { el.classList.toggle("ativo", j === i); });
      if (lista.children[i]) lista.children[i].scrollIntoView({ block: "nearest" });
    }
    function escolher(item) {
      var op = Array.prototype.find.call(escolha.options, function (o) { return o.value === String(item.id); });
      if (!op) { op = new Option(item.texto, item.id); escolha.add(op); }
      if (item.densidade) op.dataset.densidade = item.densidade;
      escolha.value = String(item.id);
      texto.value = item.texto;
      fechar();
      escolha.dispatchEvent(new Event("change", { bubbles: true }));
    }
    function mostrar(resultados) {
      itens = resultados;
      lista.innerHTML = "";
      if (!resultados.length) {
        var vazio = caixa.dataset.vazio || "Nada encontrado. Tente outra parte do nome, o CNPJ ou a cidade.";
        if (!texto.value.trim() && caixa.dataset.vazio) vazio = caixa.dataset.vazio;
        else if (caixa.dataset.vazio) vazio = "Nada encontrado. Tente outra parte do nome ou o código.";
        lista.innerHTML = '<div class="busca-vazio"></div>';
        lista.firstChild.textContent = vazio;
      }
      resultados.forEach(function (r, i) {
        var el = document.createElement("div");
        el.className = "busca-item"; el.setAttribute("role", "option");
        el.innerHTML = "<b></b><span></span>";
        el.querySelector("b").textContent = r.texto + (r.fantasia && r.fantasia !== r.texto ? " (" + r.fantasia + ")" : "");
        el.querySelector("span").textContent = r.detalhe;
        el.addEventListener("mousedown", function (e) { e.preventDefault(); escolher(r); });
        el.addEventListener("mousemove", function () { marcar(i); });
        lista.appendChild(el);
      });
      lista.hidden = false;
      marcar(resultados.length ? 0 : -1);
    }
    function buscar() {
      var q = texto.value.trim(), n = ++pedido;
      var sep = url.indexOf("?") >= 0 ? "&" : "?";
      var extra = "";
      if (caixa.dataset.depende) {
        // Ex.: produto depende do cliente → manda o cliente escolhido junto.
        var outro = caixa.closest("form").querySelector('[name="' + caixa.dataset.depende + '"]');
        if (outro && outro.value) extra = "&" + caixa.dataset.depende + "=" + encodeURIComponent(outro.value);
      }
      fetch(url + sep + "q=" + encodeURIComponent(q) + extra, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (n === pedido) mostrar(d.resultados || []); });
    }
    texto.addEventListener("input", function () {
      clearTimeout(timer);
      if (escolha.value) { escolha.value = ""; escolha.dispatchEvent(new Event("change", { bubbles: true })); }
      timer = setTimeout(buscar, 200);
    });
    texto.addEventListener("focus", function () { if (!escolha.value) buscar(); });
    texto.addEventListener("keydown", function (e) {
      if (lista.hidden) { if (e.key === "ArrowDown") buscar(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); marcar(Math.min(ativo + 1, itens.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); marcar(Math.max(ativo - 1, 0)); }
      else if (e.key === "Enter") { if (itens[ativo]) { e.preventDefault(); escolher(itens[ativo]); } }
      else if (e.key === "Escape") { fechar(); }
    });
    texto.addEventListener("blur", function () {
      setTimeout(fechar, 120);
      // Saiu sem escolher: volta o nome do escolhido (ou limpa).
      setTimeout(function () {
        var op = escolha.options[escolha.selectedIndex];
        texto.value = op && op.value ? op.textContent.trim() : "";
      }, 130);
    });
  }
  document.querySelectorAll(".busca-campo").forEach(iniciar);
})();
