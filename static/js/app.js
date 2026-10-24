        // ==================== STATE ====================
        function getHojeISO() {
            const tzoffset = (new Date()).getTimezoneOffset() * 60000;
            return (new Date(Date.now() - tzoffset)).toISOString().split('T')[0];
        }

        let state = {
            token: localStorage.getItem("nutriia_token") || null,
            nome: localStorage.getItem("nutriia_nome") || "",
            metaDiaria: 2000,
            consumidas: 0,
            gastas: 0,
            prot: 0,
            carb: 0,
            gord: 0,
            perfil: null,
            dataAtual: getHojeISO()
        };

        // ==================== API HELPERS ====================
        function authHeaders() {
            return {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${state.token}`
            };
        }

        async function handleResponse(res) {
            const isJson = res.headers.get("content-type")?.includes("application/json");
            const data = isJson ? await res.json() : null;
            if (!res.ok) {
                if (res.status === 401) {
                    fazerLogout();
                    throw new Error("Sessão expirada. Faça login novamente.");
                }
                throw new Error(data?.detail || "Erro na requisição");
            }
            return data;
        }

        async function apiPost(url, body) {
            // Evita ficar "Calculando" indefinidamente: aborta após 75 s
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), 75000);
            try {
                const res = await fetch(url, {
                    method: "POST",
                    headers: authHeaders(),
                    body: JSON.stringify(body),
                    signal: ctrl.signal
                });
                return await handleResponse(res);
            } catch (err) {
                if (err.name === "AbortError") {
                    throw new Error("A análise demorou mais que o esperado. Tente novamente em instantes.");
                }
                throw err;
            } finally {
                clearTimeout(timer);
            }
        }

        async function apiGet(url) {
            const res = await fetch(url, { headers: authHeaders() });
            return handleResponse(res);
        }

        async function apiPut(url, body) {
            const res = await fetch(url, {
                method: "PUT",
                headers: authHeaders(),
                body: JSON.stringify(body)
            });
            return handleResponse(res);
        }

        async function apiDelete(url) {
            const res = await fetch(url, { method: "DELETE", headers: authHeaders() });
            return handleResponse(res);
        }

        // ==================== SEGURANÇA & ESCAPE ====================
        function escapeHtml(str) {
            if (str === null || str === undefined) return "";
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        // ==================== TOAST ====================
        function toast(msg, tipo = "success") {
            const container = document.getElementById("toast-container");
            const div = document.createElement("div");
            div.className = `toast ${tipo === "error" ? "toast-error" : ""}`;
            div.innerHTML = `<span class="toast-icon">${tipo === "error" ? "" : ""}</span><span>${escapeHtml(msg)}</span>`;
            container.appendChild(div);
            setTimeout(() => div.remove(), 3500);
        }

        // ==================== NAVEGAÇÃO ====================
        function formatarDataDisplay(iso) {
            if (iso === getHojeISO()) return "Hoje";
            const partes = iso.split('-');
            return `${partes[2]}/${partes[1]}/${partes[0]}`;
        }

        async function mudarData(delta) {
            const dt = new Date(state.dataAtual + 'T12:00:00Z');
            dt.setDate(dt.getDate() + delta);
            state.dataAtual = dt.toISOString().split('T')[0];

            const displayStr = formatarDataDisplay(state.dataAtual);
            const elExtrato = document.getElementById("data-display-extrato");
            if (elExtrato) elExtrato.textContent = displayStr;

            await carregarLogsDoDia();
        }

        function navegar(tab) {
            document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
            document.getElementById(`screen-${tab}`).classList.add("active");

            document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
            const navBtn = document.querySelector(`.nav-item[data-tab="${tab}"]`);
            if (navBtn) navBtn.classList.add("active");

            if (tab === "extrato") renderizarExtrato();
            if (tab === "progresso") carregarProgresso();
        }



        // ==================== AUTH ====================
        function trocarAuthTab(tab) {
            document.querySelectorAll(".auth-tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".auth-form").forEach(f => f.classList.remove("active"));

            if (tab === "login") {
                document.querySelectorAll(".auth-tab")[0].classList.add("active");
                document.getElementById("form-login").classList.add("active");
            } else if (tab === "registrar") {
                document.querySelectorAll(".auth-tab")[1].classList.add("active");
                document.getElementById("form-registrar").classList.add("active");
            } else if (tab === "recuperar") {
                document.getElementById("form-recuperar").classList.add("active");
            } else if (tab === "redefinir") {
                document.getElementById("form-redefinir").classList.add("active");
            }
        }

        async function fazerLogin(e) {
            e.preventDefault();
            const btn = document.getElementById("btn-login");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                const data = await fetch("/api/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        email: document.getElementById("login-email").value,
                        senha: document.getElementById("login-senha").value
                    })
                }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail); return d; }));

                state.token = data.token;
                state.nome = data.nome;
                localStorage.setItem("nutriia_token", data.token);
                localStorage.setItem("nutriia_nome", data.nome);

                await inicializarApp();
                toast(`Bem-vindo de volta, ${data.nome}!`);
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Entrar";
            }
        }

        async function fazerRegistro(e) {
            e.preventDefault();
            const btn = document.getElementById("btn-registrar");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                const data = await fetch("/api/auth/registrar", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        nome: document.getElementById("reg-nome").value,
                        email: document.getElementById("reg-email").value,
                        senha: document.getElementById("reg-senha").value
                    })
                }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail); return d; }));

                state.token = data.token;
                state.nome = data.nome;
                localStorage.setItem("nutriia_token", data.token);
                localStorage.setItem("nutriia_nome", data.nome);

                await inicializarApp();
                toast(`Conta criada! Bem-vindo, ${data.nome}! `);
                // Ir para perfil para configurar dados
                navegar("perfil");
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Criar Conta";
            }
        }

        async function fazerRecuperacaoSenha(e) {
            e.preventDefault();
            const btn = document.getElementById("btn-recuperar");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                const data = await fetch("/api/auth/recuperar-senha", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: document.getElementById("rec-email").value })
                }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail); return d; }));

                toast(data.mensagem || "Se o e-mail estiver cadastrado, enviamos um link de recuperação.");
                trocarAuthTab('login');
                document.getElementById("login-email").value = document.getElementById("rec-email").value;
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Enviar link por e-mail";
            }
        }

        async function fazerRedefinicaoSenha(e) {
            e.preventDefault();
            const senha = document.getElementById("redef-senha").value;
            if (senha !== document.getElementById("redef-senha2").value) {
                toast("As senhas não coincidem.", "error");
                return;
            }
            const btn = document.getElementById("btn-redefinir");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                await fetch("/api/auth/redefinir-senha", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ token: tokenRedefinicao, nova_senha: senha })
                }).then(r => r.json().then(d => { if (!r.ok) throw new Error(d.detail); return d; }));

                tokenRedefinicao = null;
                document.getElementById("form-redefinir").reset();
                trocarAuthTab('login');
                toast("Senha redefinida! Entre com a nova senha.");
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Salvar nova senha";
            }
        }

        async function fazerAlteracaoSenha(e) {
            e.preventDefault();
            const btn = document.getElementById("btn-alterar-senha");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="border-color: var(--accent-lavender); border-top-color: transparent;"></span>';

            try {
                await apiPost("/api/auth/alterar-senha", {
                    senha_atual: document.getElementById("senha-atual").value,
                    nova_senha: document.getElementById("nova-senha").value
                });
                toast("Senha alterada com sucesso!");
                document.getElementById("form-alterar-senha").reset();
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Salvar Nova Senha";
            }
        }

        function fazerLogout() {
            state.token = null;
            state.nome = "";
            localStorage.removeItem("nutriia_token");
            localStorage.removeItem("nutriia_nome");
            document.getElementById("bottom-nav").style.display = "none";
            document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
            document.getElementById("screen-auth").classList.add("active");
        }

        // ==================== APP INIT ====================
        async function inicializarApp() {
            try {
                const me = await apiGet("/api/auth/me");
                if (me.token) {
                    state.token = me.token;
                    localStorage.setItem("nutriia_token", me.token);
                }
                state.nome = me.nome;
                state.perfil = me.perfil;

                document.getElementById("user-nome").textContent = me.nome;
                document.getElementById("profile-nome").textContent = me.nome;

                if (me.foto_perfil) {
                    const imgHtml = `<img src="${me.foto_perfil}" alt="Avatar" style="width: 100%; height: 100%; object-fit: cover; object-position: center;">`;
                    document.getElementById("avatar-emoji").innerHTML = imgHtml;
                    if (document.getElementById("header-avatar")) document.getElementById("header-avatar").innerHTML = imgHtml;
                } else {
                    const letra = me.nome.charAt(0).toUpperCase();
                    document.getElementById("avatar-emoji").textContent = letra;
                    if (document.getElementById("header-avatar")) document.getElementById("header-avatar").textContent = letra;
                }

                if (me.perfil) {
                    preencherFormPerfil(me.perfil);
                    state.metaDiaria = me.perfil.meta_calorica || 2000;
                    exibirResultadosPerfil(me.perfil);
                }

                // Carregar logs do dia
                await carregarLogsDoDia();

                // Carregar dica do dia (apenas na inicialização)
                const elDica = document.getElementById("dica-do-dia-texto");
                if (elDica) {
                    apiGet(`/api/logs/dica-do-dia/${state.dataAtual}`).then(res => {
                        elDica.textContent = `"${res.dica}"`;
                    }).catch(() => {
                        elDica.textContent = `"Seja constante. O que você faz hoje constrói o resultado de amanhã."`;
                    });
                }

                // Mostrar app
                document.getElementById("screen-auth").classList.remove("active");
                document.getElementById("bottom-nav").style.display = "flex";
                navegar("home");
            } catch (err) {
                fazerLogout();
            }
        }

        function preencherFormPerfil(p) {
            if (p.peso_kg) document.getElementById("peso").value = p.peso_kg;
            if (p.altura_cm) document.getElementById("altura").value = p.altura_cm;
            if (p.idade) document.getElementById("idade").value = p.idade;
            if (p.sexo) document.getElementById("sexo").value = p.sexo;
            if (p.nivel_atividade) document.getElementById("atividade").value = p.nivel_atividade;
            if (p.objetivo) document.getElementById("objetivo").value = p.objetivo;
        }

        function exibirResultadosPerfil(p) {
            if (p.imc) {
                document.getElementById("perfil-resultados").style.display = "block";
                document.getElementById("res-imc").textContent = p.imc;
                document.getElementById("res-tmb").textContent = `${p.tmb} kcal`;
                document.getElementById("res-meta").textContent = `${p.meta_calorica} kcal`;
            }
        }

        // ==================== DASHBOARD ====================
        function atualizarDashboard() {
            const meta = Math.round(state.metaDiaria);
            const consumidas = Math.round(state.consumidas);
            const gastas = Math.round(state.gastas);
            const saldo = meta - consumidas + gastas;

            document.getElementById("home-meta").textContent = `${meta}`;
            document.getElementById("home-restante").textContent = `${saldo}`;
            
            // Circular progress
            const metaTotal = meta + gastas;
            const porcentagem = metaTotal > 0 ? Math.min(consumidas / metaTotal, 1) : 0;
            const percCirc = document.getElementById("home-percentage");
            if (percCirc) percCirc.textContent = `${Math.round(porcentagem * 100)}%`;
            
            const circleBar = document.getElementById("home-circular-bar");
            if (circleBar) {
                circleBar.setAttribute("stroke-dasharray", `${porcentagem * 100}, 100`);
            }
            
            const linearBar = document.getElementById("home-linear-bar");
            if (linearBar) {
                linearBar.style.width = `${porcentagem * 100}%`;
                if (consumidas > metaTotal) {
                    linearBar.style.background = "#F28C8C"; // Redish if over
                } else {
                    linearBar.style.background = "#20B286"; // Teal
                }
            }

            // Hoje List
            if (document.getElementById("hoje-alimentacao-kcal")) document.getElementById("hoje-alimentacao-kcal").textContent = consumidas;
            if (document.getElementById("hoje-exercicios-kcal")) document.getElementById("hoje-exercicios-kcal").textContent = gastas;
            
            // Agua List
            const aguaMeta = 2000;
            const agua = state.agua || 0;
            const aguaPerc = Math.min(agua / aguaMeta, 1) * 100;
            
            if (document.getElementById("hoje-agua-ml")) document.getElementById("hoje-agua-ml").textContent = agua;
            if (document.getElementById("hoje-agua-fill")) document.getElementById("hoje-agua-fill").style.width = `${aguaPerc}%`;
            
            // Progresso Bottom
            if (document.getElementById("prog-bottom-meta")) document.getElementById("prog-bottom-meta").textContent = meta;
            if (document.getElementById("prog-bottom-peso")) document.getElementById("prog-bottom-peso").textContent = `${state.perfil ? state.perfil.peso_kg || 0 : 0} kg`;
            if (document.getElementById("prog-bottom-agua")) document.getElementById("prog-bottom-agua").textContent = `${agua} ml`;
        }

        // ==================== LOGS ====================
        let logsHoje = [];

        async function carregarLogsDoDia() {
            try {
                logsHoje = await apiGet(`/api/logs/data/${state.dataAtual}`);
                const aguaRes = await apiGet(`/api/agua/${state.dataAtual}`).catch(() => ({ total_ml: 0 }));

                state.consumidas = 0;
                state.gastas = 0;
                state.prot = 0;
                state.carb = 0;
                state.gord = 0;
                state.agua = aguaRes.total_ml || 0;

                logsHoje.forEach(log => {
                    if (log.tipo === "refeicao") {
                        state.consumidas += log.calorias;
                        state.prot += (log.proteinas_g || 0);
                        state.carb += (log.carboidratos_g || 0);
                        state.gord += (log.gorduras_g || 0);
                    } else if (log.tipo === "exercicio") {
                        state.gastas += log.calorias;
                    }
                });

                atualizarDashboard();

                if (document.getElementById("screen-extrato").classList.contains("active")) {
                    renderizarExtrato();
                }
            } catch (err) {
                console.error("Erro ao carregar logs:", err);
            }
        }

        function abrirModalDetalhes(id) {
            const log = logsHoje.find(l => l.id === id);
            if (!log) return;

            document.getElementById("detalhes-titulo").textContent = log.tag;
            document.getElementById("detalhes-desc").textContent = log.descricao;

            let html = `
                <div class="detalhes-item"><span>Calorias</span><strong style="color: var(--accent-${log.cor})">${log.titulo}</strong></div>
            `;
            if (log.tipo === "refeicao") {
                html += `
                    <div class="detalhes-item"><span>Proteínas</span><strong>${log.proteinas_g}g</strong></div>
                    <div class="detalhes-item"><span>Carboidratos</span><strong>${log.carboidratos_g}g</strong></div>
                    <div class="detalhes-item"><span>Gorduras</span><strong>${log.gorduras_g}g</strong></div>
                `;
            } else {
                html += `<div class="detalhes-item"><span>Detalhes</span><strong>${log.detalhes}</strong></div>`;
            }
            document.getElementById("detalhes-macros").innerHTML = html;

            const dicaEl = document.getElementById("detalhes-dica");
            if (log.dica_ia) {
                document.getElementById("detalhes-dica-texto").textContent = log.dica_ia;
                dicaEl.style.display = "block";
            } else {
                dicaEl.style.display = "none";
            }

            document.getElementById("modal-detalhes").classList.add("active");
        }

        function fecharModalDetalhes() {
            document.getElementById("modal-detalhes").classList.remove("active");
        }

        function criarCardLog(log, showActions = false) {
            const isRefeicao = log.tipo === "refeicao";
            const cor = isRefeicao ? "green" : "orange";

            let actionsHtml = "";
            if (showActions) {
                actionsHtml = `
                    <div class="log-actions">
                        <button class="btn-icon" onclick="event.stopPropagation(); abrirModalEdicao(${log.id})"><span class="svg-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></span></button>
                        <button class="btn-icon" onclick="event.stopPropagation(); excluirLog(${log.id})"><span class="svg-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></span></button>
                    </div>
                `;
            }

            return `
                <div class="log-card" onclick="abrirModalDetalhes(${log.id})">
                    <div class="log-card-left">
                        <span class="log-tag log-tag-${cor}">${escapeHtml(log.tag)}</span>
                        <div class="log-desc">${escapeHtml(log.descricao)}</div>
                        <div class="log-details">${escapeHtml(log.detalhes)}</div>
                        ${actionsHtml}
                    </div>
                    <div class="log-kcal log-kcal-${cor}">${escapeHtml(log.titulo)}</div>
                </div>
            `;
        }


        function renderizarExtrato() {
            const container = document.getElementById("extrato-logs");
            if (logsHoje.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-icon"><span class="svg-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg></span></div>
                        <p>Nenhum registro para esta data.</p>
                    </div>
                `;
                return;
            }
            container.innerHTML = logsHoje.map(l => criarCardLog(l, true)).join("");
        }

        // ==================== LOADING UX ====================
        let loadingTimers = [];

        function mostrarLoadingMagico() {
            const overlay = document.getElementById("magic-loading-overlay");
            const textEl = document.getElementById("magic-loading-text");
            const subEl = document.getElementById("magic-loading-sub");
            loadingTimers.forEach(clearTimeout);
            overlay.classList.add("active");
            textEl.innerText = "Analisando dados...";
            subEl.innerText = "";
            loadingTimers = [
                setTimeout(() => { subEl.innerText = "Consultando a base nutricional..."; }, 6000),
                setTimeout(() => { subEl.innerText = "A IA está com bastante demanda, quase lá..."; }, 20000),
                setTimeout(() => { subEl.innerText = "Isso está levando mais tempo que o normal. Só mais um instante..."; }, 40000),
            ];
        }

        function esconderLoadingMagico() {
            const overlay = document.getElementById("magic-loading-overlay");
            loadingTimers.forEach(clearTimeout);
            loadingTimers = [];
            overlay.classList.remove("active");
        }

        function abrirModalFoto() {
            document.getElementById("modal-foto").classList.add("active");
        }

        function fecharModalFoto() {
            document.getElementById("modal-foto").classList.remove("active");
            removerRefeicaoFoto();
            document.getElementById("refeicao-desc-foto").value = "";
        }

        function abrirFormManual() {
            document.getElementById("refeicao-escolha-container").style.display = "none";
            document.getElementById("form-refeicao-manual").style.display = "block";
        }

        function voltarEscolhaRefeicao() {
            const container = document.getElementById("refeicao-escolha-container");
            if (container) container.style.display = "block";

            const form = document.getElementById("form-refeicao-manual");
            if (form && container) form.style.display = "none";
        }

        // ==================== FOTO REFEIÇÃO ====================
        let refeicaoFotoBase64 = null;

        function removerRefeicaoFoto() {
            refeicaoFotoBase64 = null;
            document.getElementById("refeicao-foto").value = "";
            document.getElementById("refeicao-foto-preview-container").style.display = "none";
            document.getElementById("btn-tirar-foto").style.display = "flex";
        }

        function previewRefeicaoFoto(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = function (event) {
                const img = new Image();
                img.onload = function () {
                    const canvas = document.createElement('canvas');
                    const MAX_WIDTH = 800;
                    const MAX_HEIGHT = 800;
                    let width = img.width;
                    let height = img.height;

                    if (width > height) {
                        if (width > MAX_WIDTH) {
                            height *= MAX_WIDTH / width;
                            width = MAX_WIDTH;
                        }
                    } else {
                        if (height > MAX_HEIGHT) {
                            width *= MAX_HEIGHT / height;
                            height = MAX_HEIGHT;
                        }
                    }
                    canvas.width = width;
                    canvas.height = height;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0, width, height);

                    refeicaoFotoBase64 = canvas.toDataURL('image/jpeg', 0.8);

                    document.getElementById("refeicao-foto-preview").src = refeicaoFotoBase64;
                    document.getElementById("btn-tirar-foto").style.display = "none";
                    document.getElementById("refeicao-foto-preview-container").style.display = "block";
                };
                img.src = event.target.result;
            };
            reader.readAsDataURL(file);
        }

        // ==================== REGISTRO RÁPIDO & ÁGUA ====================
        let registroRapidoAtual = null;
        let tipoRefeicaoAtual = null;

        function abrirRegistroRapido(tipo) {
            tipoRefeicaoAtual = tipo;
            document.getElementById("registro-rapido-titulo").textContent = `Adicionar ${tipo}`;
            document.getElementById("registro-rapido-desc").value = "";
            document.getElementById("registro-rapido-step1").style.display = "block";
            document.getElementById("registro-rapido-step2").style.display = "none";
            document.getElementById("registro-rapido-step-manual").style.display = "none";
            const boxFalha = document.getElementById("registro-rapido-falha-ia");
            if (boxFalha) boxFalha.style.display = "none";

            // Limpar campos manuais
            document.getElementById("manual-alimento").value = "";
            document.getElementById("manual-quantidade").value = "1";
            document.getElementById("manual-unidade").value = "unidade";
            document.getElementById("manual-calorias").value = "";
            document.getElementById("manual-prot").value = "0";
            document.getElementById("manual-carb").value = "0";
            document.getElementById("manual-gord").value = "0";
            document.getElementById("manual-aprender").checked = true;

            document.getElementById("modal-registro-rapido").classList.add("active");
        }

        function fecharRegistroRapido() {
            document.getElementById("modal-registro-rapido").classList.remove("active");
            registroRapidoAtual = null;
        }

        function abrirRegistroManualStep(vindoDoStep2 = false) {
            document.getElementById("registro-rapido-step1").style.display = "none";
            document.getElementById("registro-rapido-step2").style.display = "none";
            document.getElementById("registro-rapido-step-manual").style.display = "block";

            const desc = (document.getElementById("registro-rapido-desc").value || "").trim();
            const inputAlimento = document.getElementById("manual-alimento");
            if (!inputAlimento.value && desc) {
                inputAlimento.value = desc;
            }

            if (vindoDoStep2 && registroRapidoAtual) {
                if (registroRapidoAtual.itens_detalhados && registroRapidoAtual.itens_detalhados.length > 0) {
                    inputAlimento.value = registroRapidoAtual.itens_detalhados[0].nome || desc;
                    document.getElementById("manual-quantidade").value = registroRapidoAtual.itens_detalhados[0].quantidade_original || 1;
                    document.getElementById("manual-unidade").value = registroRapidoAtual.itens_detalhados[0].unidade_original || "unidade";
                }
                if (registroRapidoAtual.total_kcal) {
                    document.getElementById("manual-calorias").value = registroRapidoAtual.total_kcal;
                }
                if (registroRapidoAtual.proteinas_g !== undefined) document.getElementById("manual-prot").value = registroRapidoAtual.proteinas_g;
                if (registroRapidoAtual.carboidratos_g !== undefined) document.getElementById("manual-carb").value = registroRapidoAtual.carboidratos_g;
                if (registroRapidoAtual.gorduras_g !== undefined) document.getElementById("manual-gord").value = registroRapidoAtual.gorduras_g;
            }
        }

        function voltarDoManual() {
            document.getElementById("registro-rapido-step-manual").style.display = "none";
            document.getElementById("registro-rapido-step1").style.display = "block";
        }

        async function salvarRefeicaoManual() {
            const alimento = (document.getElementById("manual-alimento").value || "").trim();
            const quantidade = parseFloat(document.getElementById("manual-quantidade").value) || 1;
            const unidade = (document.getElementById("manual-unidade").value || "").trim() || "unidade";
            const calorias = parseFloat(document.getElementById("manual-calorias").value);
            const proteinas_g = parseFloat(document.getElementById("manual-prot").value) || 0;
            const carboidratos_g = parseFloat(document.getElementById("manual-carb").value) || 0;
            const gorduras_g = parseFloat(document.getElementById("manual-gord").value) || 0;
            const aprender = document.getElementById("manual-aprender").checked;

            if (!alimento) {
                toast("Informe o nome do alimento", "error");
                return;
            }
            if (isNaN(calorias) || calorias < 0) {
                toast("Informe o valor de calorias (kcal)", "error");
                return;
            }

            mostrarLoadingMagico();
            try {
                await apiPost("/api/nutricao/salvar-manual", {
                    tipo: tipoRefeicaoAtual || "Refeição",
                    alimento: alimento,
                    quantidade: quantidade,
                    unidade: unidade,
                    calorias: calorias,
                    proteinas_g: proteinas_g,
                    carboidratos_g: carboidratos_g,
                    gorduras_g: gorduras_g,
                    data: state.dataAtual,
                    aprender: aprender
                });

                await carregarLogsDoDia();
                fecharRegistroRapido();
                if (aprender) {
                    toast("🎉 Salvo com sucesso e a IA já aprendeu este alimento!");
                } else {
                    toast("Refeição registrada com sucesso!");
                }
            } catch (err) {
                toast(err.message || "Erro ao salvar refeição", "error");
            } finally {
                esconderLoadingMagico();
            }
        }

        async function analisarRegistroRapido() {
            const desc = document.getElementById("registro-rapido-desc").value;
            if (!desc.trim()) {
                toast("O que você comeu?", "error");
                return;
            }

            mostrarLoadingMagico();
            try {
                const data = await apiPost("/api/nutricao/pre-analisar", {
                    tipo: tipoRefeicaoAtual,
                    descricao: desc,
                    data: state.dataAtual
                });

                registroRapidoAtual = { ...data, tipo: tipoRefeicaoAtual, descricao: desc };

                // Mostrar Step 2
                document.getElementById("registro-rapido-nome-refeicao").textContent = tipoRefeicaoAtual;
                document.getElementById("registro-rapido-kcal").textContent = data.total_kcal;
                document.getElementById("registro-rapido-p").textContent = data.proteinas_g;
                document.getElementById("registro-rapido-c").textContent = data.carboidratos_g;
                document.getElementById("registro-rapido-g").textContent = data.gorduras_g;
                document.getElementById("registro-rapido-dica").textContent = data.dica_ia;
                document.getElementById("registro-rapido-confianca").textContent = data.nivel_confianca;
                
                const ul = document.getElementById("registro-rapido-itens");
                ul.innerHTML = (data.itens_detalhados || []).map(item => {
                    const peso = item.peso_g ? ` (${Math.round(item.peso_g)} g/ml)` : "";
                    const fonte = item.fonte && item.fonte.startsWith("Base") ? "base de referência" : "estimativa";
                    return `<li><strong>${escapeHtml(item.nome)}</strong> — ${escapeHtml(item.quantidade_original)} ${escapeHtml(item.unidade_original)}${peso} · ${Math.round(item.kcal)} kcal <small style="opacity:.6">(${escapeHtml(fonte)})</small></li>`;
                }).join("");

                document.getElementById("registro-rapido-step1").style.display = "none";
                document.getElementById("registro-rapido-step2").style.display = "block";
            } catch (err) {
                toast(err.message, "error");
                // Exibir opção para preenchimento manual imediatamente
                const boxFalha = document.getElementById("registro-rapido-falha-ia");
                if (boxFalha) {
                    boxFalha.style.display = "block";
                    const inputAlimento = document.getElementById("manual-alimento");
                    if (inputAlimento && !inputAlimento.value) {
                        inputAlimento.value = desc;
                    }
                }
            } finally {
                esconderLoadingMagico();
            }
        }

        function voltarRegistroRapido() {
            document.getElementById("registro-rapido-step1").style.display = "block";
            document.getElementById("registro-rapido-step2").style.display = "none";
        }

        async function confirmarRegistroRapido() {
            if (!registroRapidoAtual) return;
            mostrarLoadingMagico();
            try {
                await apiPost("/api/logs/salvar-refeicao", {
                    ...registroRapidoAtual,
                    data: state.dataAtual
                });
                await carregarLogsDoDia();
                toast("Refeição salva com sucesso!");
                fecharRegistroRapido();
            } catch (err) {
                toast(err.message, "error");
            } finally {
                esconderLoadingMagico();
            }
        }

        // ==================== MODAL EXERCÍCIO ====================
        function abrirModalExercicio() {
            document.getElementById("modal-exercicio-desc").value = "";
            document.getElementById("modal-exercicio-duracao").value = "";
            document.getElementById("modal-exercicio-intensidade").value = "Moderada";
            document.getElementById("modal-exercicio").classList.add("active");
        }

        function fecharModalExercicio() {
            document.getElementById("modal-exercicio").classList.remove("active");
        }

        async function registrarExercicioModal() {
            if (!state.perfil) {
                toast("Configure seu perfil antes de registrar exercícios!", "error");
                navegar("perfil");
                return;
            }

            const descricao = document.getElementById("modal-exercicio-desc").value;
            const duracao = parseInt(document.getElementById("modal-exercicio-duracao").value);
            const intensidade = document.getElementById("modal-exercicio-intensidade").value;

            if (!descricao.trim()) { toast("Informe o exercício.", "error"); return; }
            if (!duracao || duracao <= 0) { toast("Informe a duração.", "error"); return; }

            fecharModalExercicio();
            mostrarLoadingMagico();

            try {
                await apiPost("/api/exercicio/analisar", {
                    descricao,
                    duracao_minutos: duracao,
                    intensidade,
                    peso_kg: state.perfil.peso_kg,
                    data: state.dataAtual
                });

                await carregarLogsDoDia();
                toast("Exercício registrado!");
                if (logsHoje.length > 0) abrirModalDetalhes(logsHoje[0].id);
            } catch (err) {
                toast(err.message, "error");
            } finally {
                esconderLoadingMagico();
            }
        }

        // ==================== REGISTRAR REFEIÇÃO ====================
        async function registrarRefeicao(e, isFotoMode) {
            if (e) e.preventDefault();
            if (!state.perfil) {
                toast("Configure seu perfil antes de registrar refeições!", "error");
                navegar("perfil");
                return;
            }

            let tipo, descricao;
            if (isFotoMode) {
                tipo = document.getElementById("refeicao-tipo-foto").value;
                descricao = document.getElementById("refeicao-desc-foto").value;
                if (!refeicaoFotoBase64) {
                    toast("Por favor, tire ou anexe uma foto!", "error");
                    return;
                }
                fecharModalFoto();
            } else {
                tipo = document.getElementById("refeicao-tipo-manual").value;
                descricao = document.getElementById("refeicao-desc-manual").value;
                if (!descricao.trim()) {
                    toast("Por favor, descreva a refeição!", "error");
                    return;
                }
            }

            mostrarLoadingMagico();

            try {
                const dataRes = await apiPost("/api/nutricao/analisar", {
                    tipo,
                    descricao,
                    data: state.dataAtual,
                    foto_base64: isFotoMode ? refeicaoFotoBase64 : null
                });

                await carregarLogsDoDia();
                if (!isFotoMode) {
                    document.getElementById("refeicao-desc-manual").value = "";
                    // voltarEscolhaRefeicao();
                }
                toast(`${tipo} registrado!`);
                if (logsHoje.length > 0) abrirModalDetalhes(logsHoje[0].id);
            } catch (err) {
                toast(err.message, "error");
            } finally {
                esconderLoadingMagico();
            }
        }



        // ==================== EDITAR E EXCLUIR ====================
        async function excluirLog(id) {
            if (!confirm("Tem certeza que deseja excluir este registro?")) return;
            try {
                await apiDelete(`/api/logs/${id}`);
                toast("Registro excluído.");
                await carregarLogsDoDia();
            } catch (err) {
                toast("Erro ao excluir", "error");
            }
        }

        function fecharModalEdicao() {
            document.getElementById("modal-editar").classList.remove("active");
        }

        function abrirModalEdicao(id) {
            const log = logsHoje.find(l => l.id === id);
            if (!log) return;

            document.getElementById("edit-id").value = log.id;
            document.getElementById("edit-desc").value = log.descricao;
            document.getElementById("edit-calorias").value = log.calorias;
            document.getElementById("edit-prot").value = log.proteinas_g || 0;
            document.getElementById("edit-carb").value = log.carboidratos_g || 0;
            document.getElementById("edit-gord").value = log.gorduras_g || 0;

            document.getElementById("modal-editar").classList.add("active");
        }

        async function salvarEdicao(e) {
            e.preventDefault();
            const id = document.getElementById("edit-id").value;
            const btn = document.getElementById("btn-salvar-edicao");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                const dados = {
                    descricao: document.getElementById("edit-desc").value,
                    calorias: parseFloat(document.getElementById("edit-calorias").value),
                    proteinas_g: parseFloat(document.getElementById("edit-prot").value) || 0,
                    carboidratos_g: parseFloat(document.getElementById("edit-carb").value) || 0,
                    gorduras_g: parseFloat(document.getElementById("edit-gord").value) || 0
                };

                await apiPut(`/api/logs/${id}`, dados);

                toast("Registro atualizado!");
                fecharModalEdicao();
                await carregarLogsDoDia();
            } catch (err) {
                toast("Erro ao atualizar", "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = 'Salvar';
            }
        }

        // ==================== PROGRESSO ====================
        async function carregarProgresso() {
            const container = document.getElementById("progresso-lista");
            container.innerHTML = '<div class="empty-state"><div class="spinner"></div></div>';
            try {
                const resumo = await apiGet("/api/logs/resumo_semanal");

                let html = '';
                resumo.slice().reverse().forEach(dia => {
                    const dataFormatada = formatarDataDisplay(dia.data);
                    html += `
                        <div class="progress-item">
                            <div class="progress-header">
                                <span>${dataFormatada}</span>
                                <span style="color: var(--accent-lavender-text)">${dia.consumidas} kcal</span>
                            </div>
                            <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 8px;">
                                P: ${Math.round(dia.proteinas)}g | C: ${Math.round(dia.carboidratos)}g | G: ${Math.round(dia.gorduras)}g
                            </div>
                            <div class="progress-bar-bg">
                                <div class="progress-bar-fill" style="width: ${Math.min((dia.consumidas / state.metaDiaria) * 100, 100)}%; background: ${dia.consumidas > state.metaDiaria ? 'var(--accent-danger)' : 'var(--accent-lavender)'}"></div>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            } catch (err) {
                container.innerHTML = '<div class="empty-state"><p>Erro ao carregar dados.</p></div>';
            }
        }

        function fecharModalAnaliseGeral() {
            document.getElementById("modal-analise-geral").classList.remove("active");
        }

        async function analisarDia() {
            mostrarLoadingMagico();
            try {
                const res = await apiGet(`/api/logs/analisar-dia/${state.dataAtual}`);
                document.getElementById("analise-geral-titulo").textContent = `Análise do Dia (${formatarDataDisplay(state.dataAtual)})`;
                document.getElementById("analise-geral-texto").innerHTML = escapeHtml(res.analise || "").replace(/\n/g, '<br>');
                document.getElementById("modal-analise-geral").classList.add("active");
            } catch (err) {
                toast(err.message, "error");
            } finally {
                esconderLoadingMagico();
            }
        }

        async function analisarSemana() {
            mostrarLoadingMagico();
            try {
                const res = await apiGet("/api/logs/analisar-semana");
                document.getElementById("analise-geral-titulo").textContent = "Análise da Semana";
                document.getElementById("analise-geral-texto").innerHTML = escapeHtml(res.analise || "").replace(/\n/g, '<br>');
                document.getElementById("modal-analise-geral").classList.add("active");
            } catch (err) {
                toast(err.message, "error");
            } finally {
                esconderLoadingMagico();
            }
        }

        // ==================== FOTO DE PERFIL ====================
        // Reduz a foto antes de enviar (máx. 512 px, JPEG) — upload mais leve e rápido
        function redimensionarImagem(dataUrl, max) {
            return new Promise(resolve => {
                const img = new Image();
                img.onload = () => {
                    const r = Math.min(1, max / Math.max(img.width, img.height));
                    const c = document.createElement("canvas");
                    c.width = Math.round(img.width * r);
                    c.height = Math.round(img.height * r);
                    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
                    resolve(c.toDataURL("image/jpeg", 0.85));
                };
                img.onerror = () => resolve(dataUrl);
                img.src = dataUrl;
            });
        }

        async function uploadFoto(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = async function (event) {
                const base64 = await redimensionarImagem(event.target.result, 512);
                // Update UI Immediately
                const imgHtml = `<img src="${base64}" alt="Avatar" style="width: 100%; height: 100%; object-fit: cover; object-position: center;">`;
                document.getElementById("avatar-emoji").innerHTML = imgHtml;
                if (document.getElementById("header-avatar")) document.getElementById("header-avatar").innerHTML = imgHtml;

                try {
                    await apiPost("/api/perfil/foto", { foto_base64: base64 });
                    toast("Foto de perfil atualizada!");
                } catch (err) {
                    toast("Erro ao salvar foto", "error");
                }
            };
            reader.readAsDataURL(file);
        }

        // ==================== SALVAR PERFIL ====================
        async function salvarPerfil(e) {
            e.preventDefault();
            const btn = document.getElementById("btn-perfil");
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>';

            try {
                const dados = {
                    peso_kg: parseFloat(document.getElementById("peso").value),
                    altura_cm: parseFloat(document.getElementById("altura").value),
                    idade: parseInt(document.getElementById("idade").value),
                    sexo: document.getElementById("sexo").value,
                    nivel_atividade: parseFloat(document.getElementById("atividade").value),
                    objetivo: document.getElementById("objetivo").value
                };

                const metricas = await apiPost("/api/perfil/calcular", dados);

                state.perfil = { ...dados, ...metricas };
                state.metaDiaria = metricas.meta_calorica;

                document.getElementById("perfil-resultados").style.display = "block";
                document.getElementById("res-imc").textContent = metricas.imc;
                document.getElementById("res-tmb").textContent = `${metricas.tmb} kcal`;
                document.getElementById("res-meta").textContent = `${metricas.meta_calorica} kcal`;

                atualizarDashboard();
                toast("Perfil salvo com sucesso! ");
            } catch (err) {
                toast(err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = "Salvar Perfil";
            }
        }

        // ==================== AGUA ====================
        function abrirModalAgua() {
            document.getElementById('modal-agua').classList.add('active');
            document.getElementById('input-agua-ml').value = '';
        }

        function fecharModalAgua() {
            document.getElementById('modal-agua').classList.remove('active');
        }

        async function salvarAguaManual() {
            const ml = parseInt(document.getElementById('input-agua-ml').value);
            if (!ml || ml <= 0) {
                toast('Digite uma quantidade válida.', 'error');
                return;
            }
            await enviarAgua(ml);
        }

        async function adicionarAguaRapido(ml) {
            await enviarAgua(ml);
        }

        async function enviarAgua(ml) {
            try {
                const payload = {
                    data: state.dataAtual,
                    quantidade_ml: ml
                };
                await apiPost('/api/agua', payload);
                fecharModalAgua();
                await carregarLogsDoDia();
                toast(`+${ml}ml de água adicionados!`, 'success');
            } catch (err) {
                toast('Erro ao registrar água. Tente novamente.', 'error');
            }
        }

        // ==================== INICIALIZAÇÃO ====================
        // Link de recuperação recebido por e-mail: /?reset=TOKEN
        let tokenRedefinicao = new URLSearchParams(window.location.search).get("reset");
        if (tokenRedefinicao) {
            history.replaceState(null, "", window.location.pathname); // remove o token da barra de endereço
            fazerLogout();
            trocarAuthTab('redefinir');
        } else if (state.token) {
            inicializarApp();
        }

        // Registrar Service Worker
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/sw.js').then(reg => {
                    reg.update(); // Força atualização do SW
                }).catch(() => { });
            });
        }

        // Renova os dados e o token quando voltar para o app (PWA)
        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === 'visible' && state.token) {
                apiGet("/api/auth/me").then(me => {
                    if (me.token) {
                        state.token = me.token;
                        localStorage.setItem("nutriia_token", me.token);
                    }
                }).catch(() => { });
                carregarLogsDoDia();
            }
        });
