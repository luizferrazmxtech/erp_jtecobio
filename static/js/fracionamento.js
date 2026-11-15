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
    var aviso = document.getElementById("f-sobra");
    aviso.hidden = !(restante > 0.5 && ordem.length);
    document.getElementById("f-sobra-kg").textContent = fmt(restante, 0);
  }

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
