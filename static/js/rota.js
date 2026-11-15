/* Escolha visual dos tanques de um pedido de venda.

   Tudo aqui é só para ajudar a escolher: os números são recalculados na tela
   enquanto você mexe, mas só valem depois de "Salvar tanques" (o servidor
   confere tudo de novo). A sugestão segue a mesma regra do servidor
   (apps/vendas/services.py, sugerir_origens): pega do tanque de menor custo
   efetivo NET (produto + frete, sem os impostos que voltam como crédito)
   até completar o pedido.

   Custo net, como na planilha DRE (apps/vendas/impostos.py):
     produto net = custo × fator de compra   (ICMS 12% + PIS/COFINS → 0,7986)
     frete net   = frete × 0,88              (crédito de ICMS do CT-e)
     preço net   = preço × fator de venda    (ICMS 18% em SP → 0,74415)
*/
(function () {
  var form = document.getElementById("escolha-rota");
  if (!form) return;

  var pedidoKg = Number(form.dataset.pedido);
  var preco = Number(form.dataset.preco);
  var fatorVenda = Number(form.dataset.fatorVenda) || 1;
  var fatorCompra = Number(form.dataset.fatorCompra) || 1;
  var fatorFrete = Number(form.dataset.fatorFrete) || 1;
  var precoNet = preco * fatorVenda;
  var cartoes = Array.prototype.slice.call(form.querySelectorAll(".origem"));
  var svg = document.getElementById("rota-svg");

  // ---------- números ----------
  // Aceita "1234.5", "1.234,5" e "18.000" (dezoito mil), como o servidor.
  function ler(texto) {
    texto = String(texto || "").trim();
    if (!texto) return null;
    if (texto.indexOf(",") >= 0) texto = texto.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(texto)) texto = texto.replace(/\./g, "");
    var n = Number(texto);
    return isNaN(n) ? null : n;
  }
  function fmt(n, casas) {
    return n.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }
  // Números nos campos no jeito brasileiro: 43.954,5 e 0,0900.
  function paraCampo(n, minimo, maximo) {
    return n.toLocaleString("pt-BR", { minimumFractionDigits: minimo, maximumFractionDigits: maximo });
  }

  // ---------- dados de cada tanque ----------
  var tanques = cartoes.map(function (el) {
    return {
      el: el,
      nome: el.dataset.nome,
      livre: Number(el.dataset.livre),
      saldo: Number(el.dataset.saldo),
      capacidade: Number(el.dataset.capacidade) || Number(el.dataset.saldo) || 1,
      custo: Number(el.dataset.custo),
      sugeridoServidor: Number(el.dataset.sugerido),
      frete: el.querySelector("input.frete"),
      qtd: el.querySelector("input.qtd"),
      deslizar: el.querySelector("input.deslizar"),
      cilindro: el.querySelector(".cilindro-grande"),
      salvoQtd: Number(el.dataset.salvoQtd) || 0,
      salvoFrete: el.dataset.salvoFrete === "" ? null : Number(el.dataset.salvoFrete),
    };
  });

  function freteDe(t) { return ler(t.frete.value); }
  function efetivoDe(t) { var f = freteDe(t); return f === null ? null : t.custo + f; }
  function efetivoNetDe(t) { var f = freteDe(t); return f === null ? null : t.custo * fatorCompra + f * fatorFrete; }
  function qtdDe(t) { return Math.max(0, ler(t.qtd.value) || 0); }

  // Quanto este tanque ainda pode mandar sem passar do pedido.
  function limiteDe(t) {
    var outros = tanques.reduce(function (s, o) { return o === t ? s : s + qtdDe(o); }, 0);
    return Math.max(0, Math.min(t.livre, pedidoKg - outros));
  }

  // Ordem: com frete primeiro, do menor custo efetivo net para o maior; empate → mais saldo.
  function ranking() {
    return tanques.slice().sort(function (a, b) {
      var ea = efetivoNetDe(a), eb = efetivoNetDe(b);
      if ((ea === null) !== (eb === null)) return ea === null ? 1 : -1;
      var ca = ea === null ? a.custo * fatorCompra : ea, cb = eb === null ? b.custo * fatorCompra : eb;
      return ca - cb || b.livre - a.livre;
    });
  }

  function sugestao() {
    var minimo = ler(document.getElementById("minimo").value) || 0;
    var restante = pedidoKg, saida = new Map();
    ranking().forEach(function (t) {
      if (restante <= 0) return;
      var pegar = Math.min(t.livre, restante);
      if (pegar < minimo && pegar < restante) return;
      saida.set(t, pegar);
      restante -= pegar;
    });
    return saida;
  }

  // ---------- desenho ----------
  function pct(v, base) { return Math.max(0, Math.min(100, (v * 100) / base)); }
  function texto(t, seletor, valor, casas) {
    var el = t.el.querySelector(seletor);
    el.textContent = valor === null ? "—" : fmt(valor, casas);
    el.classList.toggle("negativo", valor !== null && valor < 0);
  }

  function desenharTanque(t, sai, posicao, sugerido) {
    var livreResta = Math.max(0, t.livre - sai);
    var reservado = Math.max(0, t.saldo - t.livre);
    var c = t.cilindro;
    var hResta = pct(livreResta, t.capacidade), hSai = pct(sai, t.capacidade), hRes = pct(reservado, t.capacidade);
    c.querySelector(".nivel-livre").style.height = hResta + "%";
    var s = c.querySelector(".nivel-sai");
    s.style.bottom = hResta + "%"; s.style.height = hSai + "%";
    var r = c.querySelector(".nivel-reservado");
    r.style.bottom = (hResta + hSai) + "%"; r.style.height = hRes + "%";
    c.querySelector(".rotulo-sai").textContent = sai > 0 ? "sai " + fmt(sai / 1000, 1) + " t" : fmt(pct(t.saldo, t.capacidade), 0) + "%";

    var f = freteDe(t), ef = efetivoDe(t), efn = efetivoNetDe(t);
    texto(t, ".v-frete-net", f === null ? null : f * fatorFrete, 4);
    texto(t, ".v-efetivo", ef, 4);
    texto(t, ".v-efetivo-net", efn, 4);
    texto(t, ".v-sobra", ef === null ? null : preco - ef, 4);
    texto(t, ".v-sobra-net", efn === null ? null : precoNet - efn, 4);
    // Margem bruta − margem net = imposto da venda − créditos (produto e frete).
    var impVenda = preco - precoNet, creditos = t.custo * (1 - fatorCompra) + (f || 0) * (1 - fatorFrete);
    t.el.querySelector(".v-imposto").textContent = ef === null ? "" :
      "Imposto a pagar R$ " + fmt(impVenda - creditos, 4) + "/kg = imposto da venda R$ " + fmt(impVenda, 4) +
      " − créditos R$ " + fmt(creditos, 4);
    t.el.classList.toggle("sem-frete", ef === null);
    t.el.classList.toggle("escolhido", sai > 0);
    t.el.classList.toggle("sugerido", sugerido);
    t.el.querySelector(".posicao").textContent = posicao + "º";
    // A barra só vai até o que cabe no pedido.
    var limite = limiteDe(t);
    // Passos de 10 kg; o topo arredonda para cima para a barra alcançar o limite.
    t.deslizar.max = String(Math.max(Math.ceil(limite / PASSO) * PASSO, PASSO));
    t.deslizar.disabled = limite <= 0 && sai <= 0;
    if (document.activeElement !== t.deslizar) t.deslizar.value = String(Math.min(sai, limite));
  }

  // Linhas de cada tanque escolhido até o destino; a grossura mostra quanto sai.
  function desenharRota(escolhidos) {
    var ns = "http://www.w3.org/2000/svg";
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var caixa = svg.parentNode.getBoundingClientRect();
    var largura = Math.max(caixa.width, 280), alturaLinha = 34;
    var n = Math.max(escolhidos.length, 1), altura = Math.max(n * alturaLinha + 24, 90);
    svg.setAttribute("viewBox", "0 0 " + largura + " " + altura);
    svg.setAttribute("height", altura);
    var destinoX = largura - 120, destinoY = altura / 2, inicioX = 150;

    function el(nome, attrs, conteudo) {
      var e = document.createElementNS(ns, nome);
      Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      if (conteudo !== undefined) e.textContent = conteudo;
      svg.appendChild(e);
      return e;
    }
    if (!escolhidos.length) {
      el("text", { x: largura / 2, y: altura / 2 + 4, "text-anchor": "middle", "class": "rota-vazia" }, "Escolha um tanque para ver a rota");
      return;
    }
    escolhidos.forEach(function (item, i) {
      var y = 12 + alturaLinha * i + alturaLinha / 2;
      var grossura = 3 + 9 * (item.sai / pedidoKg);
      var meio = (inicioX + destinoX) / 2;
      el("path", { d: "M" + inicioX + " " + y + " C" + meio + " " + y + " " + meio + " " + destinoY + " " + destinoX + " " + destinoY,
                   "class": "rota-linha", "stroke-width": grossura.toFixed(1) });
      el("circle", { cx: inicioX, cy: y, r: 6, "class": "rota-no" });
      el("text", { x: inicioX - 12, y: y + 4, "text-anchor": "end", "class": "rota-nome" }, item.t.nome.length > 20 ? item.t.nome.slice(0, 19) + "…" : item.t.nome);
      var f = freteDe(item.t);
      el("text", { x: inicioX + 14, y: y - 7, "class": "rota-rotulo" },
         fmt(item.sai, 0) + " kg · frete " + (f === null ? "?" : "R$ " + fmt(f, 4)));
    });
    el("circle", { cx: destinoX, cy: destinoY, r: 10, "class": "rota-destino" });
    el("text", { x: destinoX + 16, y: destinoY - 2, "class": "rota-nome forte" }, "Cliente");
    el("text", { x: destinoX + 16, y: destinoY + 13, "class": "rota-rotulo" }, form.dataset.destino || "");
  }

  function escrever(id, valor, casas) {
    document.getElementById(id).textContent = valor === null ? "—" : fmt(valor, casas);
  }

  function atualizar() {
    var ordem = ranking(), sug = sugestao(), alocado = 0, custo = 0, custoNet = 0, semFrete = false, escolhidos = [];
    ordem.forEach(function (t, i) {
      var sai = qtdDe(t);
      desenharTanque(t, sai, i + 1, (sug.get(t) || 0) > 0);
      // A posição do cartão segue o ranking. Muda só a ordem visual (CSS),
      // sem tirar o cartão do lugar: assim o campo em que você digita não perde o foco.
      t.el.style.order = String(i);
      if (sai > 0) {
        alocado += sai;
        var f = freteDe(t);
        if (f === null) semFrete = true;
        custo += sai * (t.custo + (f || 0));
        custoNet += sai * (t.custo * fatorCompra + (f || 0) * fatorFrete);
        escolhidos.push({ t: t, sai: sai });
      }
    });

    escrever("p-alocado", alocado, 0);
    var barra = document.getElementById("p-barra");
    barra.style.width = pct(alocado, pedidoKg) + "%";
    barra.className = alocado > pedidoKg ? "excesso" : (Math.abs(alocado - pedidoKg) < 0.0005 ? "completo" : "");
    var falta = pedidoKg - alocado, aviso = document.getElementById("p-falta");
    aviso.textContent = Math.abs(falta) < 0.0005 ? "Pedido completo" : (falta > 0 ? "Faltam " + fmt(falta, 0) + " kg" : "Passou " + fmt(-falta, 0) + " kg do pedido");
    aviso.className = "pequeno " + (Math.abs(falta) < 0.0005 ? "positivo" : "negativo");

    var tem = alocado > 0;
    var margem = alocado * preco - custo, margemNet = alocado * precoNet - custoNet;
    document.getElementById("p-medio").textContent = tem ? fmt(custo / alocado, 4) + (semFrete ? "*" : "") : "—";
    document.getElementById("p-medio-net").textContent = tem ? fmt(custoNet / alocado, 4) + (semFrete ? "*" : "") : "—";
    escrever("p-custo", tem ? custo : null, 2);
    escrever("p-custo-net", tem ? custoNet : null, 2);
    escrever("p-margem", tem ? margem : null, 2);
    escrever("p-margem-net", tem ? margemNet : null, 2);
    escrever("p-imposto", tem ? margem - margemNet : null, 2);
    escrever("p-margem-net-kg", tem ? margemNet / alocado : null, 4);
    document.getElementById("p-margem-b").className = margemNet < 0 ? "negativo" : "positivo";
    desenharRota(escolhidos);
    avisarNaoSalvo();
  }

  // O resumo do pedido (lá em cima) mostra o que está gravado. Se a escolha na
  // tela for diferente, avisa para salvar: senão os dois números não batem.
  function avisarNaoSalvo() {
    var mudou = tanques.some(function (t) {
      var f = freteDe(t);
      var freteMudou = (f === null) !== (t.salvoFrete === null) || (f !== null && Math.abs(f - t.salvoFrete) > 0.00005);
      return Math.abs(qtdDe(t) - t.salvoQtd) > 0.0005 || (qtdDe(t) > 0 && freteMudou);
    });
    document.getElementById("p-nao-salvo").hidden = !mudou;
  }

  var PASSO = 10;  // a JT trabalha em toneladas: a barra anda de 10 em 10 kg

  // cru = sem ponto de milhar (enquanto a pessoa digita, para o próximo dígito não virar decimal).
  function definir(t, kg, cru) {
    kg = Math.max(0, Math.min(limiteDe(t), kg));
    if (!kg) { t.qtd.value = ""; return; }
    t.qtd.value = cru ? String(Math.round(kg * 1000) / 1000).replace(".", ",") : paraCampo(kg, 0, kg % 1 ? 3 : 0);
  }

  // ---------- eventos ----------
  tanques.forEach(function (t) {
    // Enquanto digita, o número fica como você escreveu; só corta se passar do limite.
    t.qtd.addEventListener("input", function () {
      var limite = limiteDe(t);
      if (qtdDe(t) > limite) definir(t, limite, true);
      atualizar();
    });
    // Ao sair do campo, arruma o formato (5800 → 5.800).
    t.qtd.addEventListener("change", function () { if (qtdDe(t) > 0) definir(t, qtdDe(t)); else t.qtd.value = ""; atualizar(); });
    t.frete.addEventListener("input", atualizar);
    t.frete.addEventListener("change", function () { var f = ler(t.frete.value); if (f !== null) t.frete.value = paraCampo(f, 4, 4); atualizar(); });
    // Barra: múltiplos de 10 kg; no fim dela vai até o limite exato (para esvaziar o tanque ou fechar o pedido).
    t.deslizar.addEventListener("input", function () {
      var v = Math.round(Number(t.deslizar.value) / PASSO) * PASSO, limite = limiteDe(t);
      definir(t, v >= limite - PASSO / 2 ? limite : v);
      atualizar();
    });
    // Clicar no tanque: se já usa, tira; se não, completa o que falta (até o livre).
    t.cilindro.addEventListener("click", function () {
      definir(t, qtdDe(t) > 0 ? 0 : limiteDe(t));
      atualizar();
    });
  });
  document.getElementById("usar-sugestao").addEventListener("click", function () {
    var sug = sugestao();
    tanques.forEach(function (t) { t.qtd.value = ""; });
    tanques.forEach(function (t) { definir(t, sug.get(t) || 0); });
    atualizar();
  });
  document.getElementById("zerar-tudo").addEventListener("click", function () {
    tanques.forEach(function (t) { t.qtd.value = ""; });
    atualizar();
  });
  document.getElementById("minimo").addEventListener("input", atualizar);
  window.addEventListener("resize", atualizar);

  tanques.forEach(function (t) {
    var f = ler(t.frete.value);
    if (f !== null) t.frete.value = paraCampo(f, 4, 4);
    var q = qtdDe(t);
    t.qtd.value = q ? paraCampo(q, 0, 3) : "";
  });

  // Pedido novo sem nada escolhido: já começa com a rota sugerida.
  if (!tanques.some(function (t) { return qtdDe(t) > 0; })) {
    tanques.forEach(function (t) { definir(t, t.sugeridoServidor); });
  }
  atualizar();
})();
