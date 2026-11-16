/* Nova OS de envase: calcula o fracionamento enquanto você digita.

   Mesma regra de apps/servicos/services.py (fracionar): enche primeiro os
   recipientes maiores, respeita quantos o cliente trouxe de cada tipo e põe a
   sobra num recipiente incompleto do menor tipo que ainda tiver unidade.
   O cálculo que vale é o do servidor, ao salvar; este é só a prévia. */
(function () {
  var form = document.getElementById("form-os");
  if (!form) return;
  var corpo = document.querySelector("#recipientes tbody");

  function numero(texto) {
    var t = String(texto || "").trim().replace(/\s/g, "");
    if (!t) return null;
    if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(t);
    return isNaN(n) ? null : n;
  }
  function fmt(n, casas) {
    return n.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }
  function horas(min) {
    if (!min) return "—";
    var h = Math.floor(min / 60), m = Math.round(min % 60);
    return h ? h + "h" + (m < 10 ? "0" : "") + m : m + " min";
  }
  function campo(nome) { return form.querySelector("[name=" + nome + "]"); }

  function calcular() {
    var dens = numero(campo("densidade").value) || 1;
    var qtd = numero(campo("quantidade").value) || 0;
    var kg = campo("unidade_quantidade").value === "L" ? qtd * dens : qtd;
    document.getElementById("f-kg").textContent = kg ? fmt(kg, 0) + " kg" : "—";

    var linhas = [];
    corpo.querySelectorAll("tr.linha-recip").forEach(function (tr) {
      var op = tr.querySelector(".r-tipo").selectedOptions[0];
      var l = { tr: tr, un: 0, kg: 0, parcial: 0 };
      if (op && op.value) {
        var cap = parseFloat(op.dataset.cap);
        l.cap = op.dataset.un === "L" ? cap * dens : cap;
        var d = tr.querySelector(".r-disp").value.replace(/\./g, "").trim();
        l.disp = d === "" ? null : parseInt(d, 10);
        var p = numero(tr.querySelector(".r-preco").value);
        l.preco = p === null ? parseFloat(op.dataset.preco) : p;
        l.tempo = parseFloat(op.dataset.tempo) || 0;
        l.nome = op.textContent.trim();
        tr.querySelector(".r-preco").placeholder = fmt(parseFloat(op.dataset.preco), 4);
      }
      linhas.push(l);
    });

    var restante = kg;
    var ordem = linhas.filter(function (l) { return l.cap > 0; }).sort(function (a, b) { return b.cap - a.cap; });
    ordem.forEach(function (l) {
      var cabe = Math.floor(restante / l.cap + 1e-9);
      if (l.disp !== null && !isNaN(l.disp)) cabe = Math.min(cabe, l.disp);
      l.un = cabe; l.kg = cabe * l.cap; restante -= l.kg;
    });
    if (restante > 0.0005) {
      for (var i = ordem.length - 1; i >= 0; i--) {
        var l = ordem[i];
        var livre = l.disp === null || isNaN(l.disp) || l.un < l.disp;
        if (livre && restante < l.cap) { l.parcial = restante; l.kg += restante; restante = 0; break; }
      }
    }

    var tot = { un: 0, kg: 0, valor: 0, tempo: 0 };
    linhas.forEach(function (l) {
      var c = function (s) { return l.tr.querySelector(s); };
      if (!l.cap) { ["r-un", "r-kg", "r-valor", "r-tempo"].forEach(function (k) { c("." + k).textContent = "—"; }); return; }
      var unidades = l.un + (l.parcial > 0 ? 1 : 0), valor = l.kg * l.preco, tempo = unidades * l.tempo;
      c(".r-un").innerHTML = fmt(l.un, 0) + (l.parcial > 0 ? ' <span class="sub">+ 1 com ' + fmt(l.parcial, 0) + " kg</span>" : "");
      c(".r-kg").textContent = fmt(l.kg, 0);
      c(".r-valor").textContent = fmt(valor, 2);
      c(".r-tempo").textContent = horas(tempo);
      tot.un += unidades; tot.kg += l.kg; tot.valor += valor; tot.tempo += tempo;
    });
    document.getElementById("t-un").textContent = tot.un ? fmt(tot.un, 0) + " recip." : "—";
    document.getElementById("t-kg").textContent = tot.kg ? fmt(tot.kg, 0) : "—";
    document.getElementById("t-valor").textContent = tot.valor ? "R$ " + fmt(tot.valor, 2) : "—";
    document.getElementById("t-tempo").textContent = horas(tot.tempo);
    desenharSaldo(kg, linhas.filter(function (l) { return l.cap > 0; }), restante);
  }

  /* Gráfico do saldo: uma barra com o que vai em cada tipo de recipiente,
     o que fica sem recipiente (vermelho) e a capacidade dos recipientes que
     sobram vazios (listrado). A linha "chegou" marca a carga do caminhão. */
  var CORES = ["#0e8a5f", "#2563c9", "#b7791f", "#7c3aed", "#0891b2", "#be185d"];
  function $(id) { return document.getElementById(id); }
  function desenharSaldo(kg, linhas, restante) {
    var barra = $("saldo-barra"), legenda = $("saldo-legenda"), status = $("saldo-status");
    barra.querySelectorAll(".seg").forEach(function (e) { e.remove(); });
    legenda.innerHTML = ""; $("saldo-escala").innerHTML = "";
    var falta = restante > 0.5 ? restante : 0, alocado = kg - falta;
    var vazios = 0, vaziosKg = 0, semLimite = [], cheios = 0, incompleto = null;
    linhas.forEach(function (l, i) {
      l.cor = CORES[i % CORES.length];
      l.usados = l.un + (l.parcial > 0 ? 1 : 0);
      cheios += l.un;
      if (l.parcial > 0) incompleto = l;
      if (l.disp === null || isNaN(l.disp)) { semLimite.push(l); l.sobra = 0; l.sobraKg = 0; return; }
      l.sobra = Math.max(0, l.disp - l.usados);
      l.sobraKg = l.sobra * l.cap;  // só os recipientes inteiros que voltam vazios
      vazios += l.sobra; vaziosKg += l.sobraKg;
    });
    $("s-chegou").textContent = kg ? fmt(kg, 0) + " kg" : "—";
    $("s-alocado").textContent = kg ? fmt(alocado, 0) + " kg" : "—";
    $("s-alocado-pct").textContent = kg ? fmt(cheios, 0) + " cheios" + (incompleto ? " + 1 incompleto" : "") : "";
    $("s-parcial").textContent = incompleto ? fmt(incompleto.parcial, 0) + " kg" : (kg && linhas.length ? "nenhum" : "—");
    $("s-parcial-sub").textContent = incompleto ? "num " + incompleto.nome + " (" + fmt(incompleto.parcial * 100 / incompleto.cap, 0) + "% cheio)" : "";
    $("s-falta").textContent = kg ? fmt(falta, 0) + " kg" : "—";
    var menor = linhas.slice().sort(function (a, b) { return a.cap - b.cap; })[0];
    $("s-falta-un").textContent = falta && menor ? "≈ " + fmt(Math.ceil(falta / menor.cap), 0) + " × " + menor.nome + " a mais" : "";
    $("s-vazios").textContent = linhas.length ? (semLimite.length && !vazios ? "sem limite" : fmt(vazios, 0) + " recip.") : "—";
    $("s-vazios-kg").textContent = linhas.filter(function (l) { return l.sobra > 0; }).map(function (l) { return fmt(l.sobra, 0) + " " + l.nome; }).join(" · ");

    if (!kg || !linhas.length) {
      status.className = "etiqueta cinza"; status.textContent = "Informe a quantidade e os recipientes";
      $("saldo-marca").hidden = true; return;
    }
    var resto = incompleto ? " · 1 " + incompleto.nome + " fica com " + fmt(incompleto.parcial, 0) + " kg" : "";
    if (falta) { status.className = "etiqueta vermelho"; status.textContent = "Faltam recipientes para " + fmt(falta, 0) + " kg"; }
    else if (vazios > 0) {
      var lista = linhas.filter(function (l) { return l.sobra > 0; }).map(function (l) { return fmt(l.sobra, 0) + " " + l.nome; });
      status.className = "etiqueta amarelo";
      status.textContent = "Tudo cabe" + resto + " · voltam vazios: " + lista.join(" e ");
    }
    else { status.className = "etiqueta verde"; status.textContent = "Tudo cabe" + resto + (semLimite.length || resto ? "" : ", sem sobra de recipiente"); }

    var total = Math.max(kg, alocado + vaziosKg) || 1;
    function seg(classe, valor, cor, dica) {
      if (valor <= 0) return;
      var d = document.createElement("div");
      d.className = "seg " + classe; d.style.width = (valor * 100 / total) + "%";
      if (cor) d.style.setProperty("--cor", cor);
      d.dataset.dica = dica; d.tabIndex = 0;
      barra.insertBefore(d, $("saldo-marca"));
    }
    var ordem = linhas.slice().sort(function (a, b) { return b.cap - a.cap; });
    ordem.forEach(function (l) {
      seg("cheio", l.un * l.cap, l.cor, "<b>" + l.nome + "</b><br>" + fmt(l.un, 0) + " cheios<br>" + fmt(l.un * l.cap, 0) + " kg");
    });
    if (incompleto) seg("parcial", incompleto.parcial, incompleto.cor, "<b>1 " + incompleto.nome + " incompleto</b><br>" + fmt(incompleto.parcial, 0) + " kg de " + fmt(incompleto.cap, 0) + "<br>é o resto da carga");
    seg("falta", falta, null, "<b>Sem recipiente</b><br>" + fmt(falta, 0) + " kg sobram no caminhão");
    ordem.forEach(function (l) {
      seg("vazio", l.sobraKg, l.cor, "<b>" + fmt(l.sobra, 0) + " " + l.nome + " voltam vazios</b><br>o produto acaba antes");
    });
    var marca = $("saldo-marca");
    marca.hidden = false; marca.style.left = (kg * 100 / total) + "%";
    marca.classList.toggle("direita", kg / total > .85);
    [0, .25, .5, .75, 1].forEach(function (f) {
      var t = document.createElement("span"); t.style.left = (f * 100) + "%";
      t.textContent = fmt(total * f / 1000, total < 10000 ? 1 : 0) + " t"; $("saldo-escala").appendChild(t);
    });
    ordem.forEach(function (l) {
      var item = document.createElement("span");
      item.innerHTML = '<i class="ponto" style="background:' + l.cor + '"></i>';
      var txt = l.nome + ": " + fmt(l.un, 0) + " cheios" + (l.parcial > 0 ? " + 1 com " + fmt(l.parcial, 0) + " kg" : "");
      if (l.disp !== null && !isNaN(l.disp)) txt += " · trouxe " + fmt(l.disp, 0) + (l.sobra ? " · " + fmt(l.sobra, 0) + " volta" + (l.sobra > 1 ? "m" : "") + " vazio" + (l.sobra > 1 ? "s" : "") : "");
      item.appendChild(document.createTextNode(txt));
      legenda.appendChild(item);
    });
  }

  // Dica ao passar o mouse (ou tocar) em cada pedaço da barra
  (function () {
    var barra = $("saldo-barra"), dica = $("saldo-dica"), caixa = $("saldo-envase");
    function mostrar(e) {
      var s = e.target.closest(".seg"); if (!s) { dica.hidden = true; return; }
      dica.innerHTML = s.dataset.dica; dica.hidden = false;
      var r = s.getBoundingClientRect(), c = caixa.getBoundingClientRect();
      var x = Math.min(Math.max(r.left + r.width / 2 - c.left, 90), c.width - 90);
      dica.style.left = x + "px"; dica.style.top = (r.top - c.top) + "px";
    }
    barra.addEventListener("mousemove", mostrar);
    barra.addEventListener("focusin", mostrar);
    barra.addEventListener("click", mostrar);
    barra.addEventListener("mouseleave", function () { dica.hidden = true; });
    barra.addEventListener("focusout", function () { dica.hidden = true; });
  })();

  function renumerar() {
    var n = 0;
    corpo.querySelectorAll("tr.linha-recip").forEach(function (tr) {
      tr.querySelector(".r-tipo").name = "tipo_" + n;
      tr.querySelector(".r-disp").name = "disponivel_" + n;
      tr.querySelector(".r-preco").name = "preco_" + n;
      n++;
    });
    document.getElementById("n-linhas").value = n;
  }

  document.getElementById("mais-linha").addEventListener("click", function () {
    var modelo = corpo.querySelector("tr.linha-recip");
    var nova = modelo.cloneNode(true);
    nova.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    nova.querySelector("select").value = "";
    corpo.appendChild(nova);
    renumerar(); calcular();
  });
  corpo.addEventListener("click", function (e) {
    var b = e.target.closest(".r-tirar");
    if (!b) return;
    var linhas = corpo.querySelectorAll("tr.linha-recip");
    var tr = b.closest("tr");
    if (linhas.length > 1) tr.remove(); else { tr.querySelectorAll("input").forEach(function (i) { i.value = ""; }); tr.querySelector("select").value = ""; }
    renumerar(); calcular();
  });
  campo("produto").addEventListener("change", function () {
    var op = this.options[this.selectedIndex];
    var d = op && op.dataset.densidade;
    if (d) campo("densidade").value = String(d).replace(".", ",");
    calcular();
  });
  form.addEventListener("input", calcular);
  form.addEventListener("change", calcular);
  renumerar(); calcular();
})();
