/*
 * KAVKAZ-CAR — лёгкий клиентский JS без библиотек.
 *
 * ВАЖНО: на сайте включён CSP `script-src 'self'`, поэтому inline-скрипты и
 * обработчики вида onclick="..." браузер БЛОКИРУЕТ. Всё поведение живёт здесь и
 * включается через data-атрибуты в разметке:
 *
 *   data-confirm="Текст?"        — спросить подтверждение у формы/кнопки
 *   data-autosubmit              — отправить форму при изменении поля
 *   data-toggle-param="имя"      — кнопка-фильтр: включает/выключает ?имя=1
 *   data-share                   — «Поделиться» (или копирует ссылку)
 *   data-load-more               — «Показать ещё» без перезагрузки страницы
 *   data-sheet-open / -close     — выдвижная панель фильтров
 *   data-fav-toggle / data-compare-toggle — избранное и сравнение
 *   data-photo-upload / data-photo-grid   — загрузка и порядок фото
 */
(function () {
  "use strict";

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF_TOKEN = csrfMeta ? csrfMeta.content : "";
  var isAuthenticated = document.body.getAttribute("data-authenticated") === "1";

  /* ------------------------------ Помощники -------------------------------- */

  function toast(message, type) {
    var box = document.getElementById("toasts");
    if (!box) {
      box = document.createElement("div");
      box.id = "toasts";
      box.className = "toasts";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    var el = document.createElement("div");
    el.className = "toast toast--" + (type || "info");
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () {
      el.classList.add("is-leaving");
      setTimeout(function () { el.remove(); }, 250);
    }, 3200);
  }

  function request(url, options) {
    options = options || {};
    options.method = options.method || "POST";
    options.headers = Object.assign(
      { "X-CSRF-Token": CSRF_TOKEN, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
      options.headers || {}
    );
    return fetch(url, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        data._status = res.status;
        data._ok = res.ok;
        return data;
      });
    });
  }

  function postForm(url, data) {
    return request(url, {
      body: new URLSearchParams(data || {}).toString(),
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
  }

  function storageGet(key) {
    try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* приватный режим */ }
  }

  /* ----------------------- Подтверждения и автоотправка ---------------------- */

  document.addEventListener("submit", function (evt) {
    var form = evt.target;
    var source = evt.submitter && evt.submitter.hasAttribute("data-confirm") ? evt.submitter : form;
    var message = source.getAttribute && source.getAttribute("data-confirm");
    if (message && !window.confirm(message)) evt.preventDefault();
  });

  document.addEventListener("change", function (evt) {
    var el = evt.target;
    if (el.hasAttribute && el.hasAttribute("data-autosubmit") && el.form) el.form.requestSubmit();
  });

  /* --------------------------- Быстрые фильтры каталога ---------------------- */

  document.querySelectorAll("[data-toggle-param]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var name = btn.getAttribute("data-toggle-param");
      var url = new URL(window.location.href);
      if (url.searchParams.get(name) === "1") url.searchParams.delete(name);
      else url.searchParams.set(name, "1");
      url.searchParams.delete("page");
      window.location.href = url.toString();
    });
  });

  /* ------------------------ Панель фильтров (bottom sheet) ------------------- */

  var backdropEl = document.querySelector(".sheet-backdrop");

  function closeSheets() {
    document.querySelectorAll(".bottom-sheet.is-open").forEach(function (s) { s.classList.remove("is-open"); });
    if (backdropEl) backdropEl.classList.remove("is-open");
    document.body.style.overflow = "";
  }

  document.querySelectorAll("[data-sheet-open]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sheet = document.getElementById(btn.getAttribute("data-sheet-open"));
      if (!sheet) return;
      sheet.classList.add("is-open");
      if (backdropEl) backdropEl.classList.add("is-open");
      document.body.style.overflow = "hidden";
    });
  });
  document.querySelectorAll("[data-sheet-close]").forEach(function (btn) {
    btn.addEventListener("click", closeSheets);
  });
  if (backdropEl) backdropEl.addEventListener("click", closeSheets);
  document.addEventListener("keydown", function (evt) { if (evt.key === "Escape") closeSheets(); });

  /* ------------------------------ Поделиться / копировать -------------------- */

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      area.remove();
    });
  }

  document.querySelectorAll("[data-share]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-share") || window.location.href;
      var title = document.title;
      if (navigator.share) {
        navigator.share({ title: title, url: url }).catch(function () { /* пользователь закрыл окно */ });
      } else {
        copyText(url).then(function () { toast("Ссылка скопирована", "success"); },
                           function () { toast("Не удалось скопировать ссылку", "error"); });
      }
    });
  });

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      copyText(btn.getAttribute("data-copy")).then(function () { toast("Скопировано", "success"); });
    });
  });

  /* ------------------------------ Избранное --------------------------------- */

  var FAV_KEY = "kavkazcar_favorites";

  function setLocalFavorite(publicId, isFav) {
    var list = storageGet(FAV_KEY);
    var idx = list.indexOf(publicId);
    if (isFav && idx === -1) list.push(publicId);
    if (!isFav && idx !== -1) list.splice(idx, 1);
    storageSet(FAV_KEY, list);
  }

  function markFavorites(root) {
    var local = isAuthenticated ? [] : storageGet(FAV_KEY);
    (root || document).querySelectorAll("[data-fav-toggle]").forEach(function (btn) {
      if (local.indexOf(btn.getAttribute("data-fav-toggle")) !== -1) btn.classList.add("is-active");
    });
  }

  document.addEventListener("click", function (evt) {
    var btn = evt.target.closest("[data-fav-toggle]");
    if (!btn) return;
    evt.preventDefault();
    evt.stopPropagation();
    var publicId = btn.getAttribute("data-fav-toggle");

    if (!isAuthenticated) {
      var nowActive = !btn.classList.contains("is-active");
      btn.classList.toggle("is-active", nowActive);
      setLocalFavorite(publicId, nowActive);
      toast(nowActive ? "Добавлено в избранное" : "Убрано из избранного", "success");
      return;
    }
    postForm("/favorites/toggle/" + encodeURIComponent(publicId), {}).then(function (data) {
      if (data.error) { toast(data.error, "error"); return; }
      btn.classList.toggle("is-active", !!data.favorite);
      toast(data.favorite ? "Добавлено в избранное" : "Убрано из избранного", "success");
    }).catch(function () { toast("Не удалось сохранить. Проверьте соединение.", "error"); });
  });
  markFavorites();

  // После входа переносим избранное гостя в аккаунт (один раз).
  if (isAuthenticated) {
    var pending = storageGet(FAV_KEY);
    if (pending.length) {
      postForm("/favorites/sync", { ids: pending.join(",") }).then(function (data) {
        if (data._ok) storageSet(FAV_KEY, []);
      });
    }
  }

  // Страница избранного для гостя: карточки по списку из браузера.
  var guestFav = document.getElementById("guest-favorites");
  if (guestFav && !isAuthenticated) {
    var ids = storageGet(FAV_KEY);
    var empty = document.getElementById("favorites-empty");
    if (!ids.length) {
      if (empty) empty.hidden = false;
    } else {
      fetch("/favorites/cards?ids=" + encodeURIComponent(ids.join(",")), { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (!data.count) { if (empty) empty.hidden = false; return; }
          guestFav.innerHTML = '<div class="car-grid">' + data.html + "</div>";
          markFavorites(guestFav);
          bindCompare(guestFav);
        })
        .catch(function () { guestFav.textContent = "Не удалось загрузить избранное."; });
    }
  }

  /* ------------------------------ Сравнение ---------------------------------- */

  var COMPARE_KEY = "kavkazcar_compare";

  function compareList() {
    try { return JSON.parse(sessionStorage.getItem(COMPARE_KEY) || "[]"); } catch (e) { return []; }
  }

  function renderCompareBar() {
    var bar = document.getElementById("compare-bar");
    if (!bar) return;
    var list = compareList();
    bar.hidden = list.length === 0;
    if (!list.length) return;
    bar.querySelector("[data-compare-count]").textContent = list.length;
    bar.querySelector("[data-compare-link]").href =
      "/compare?" + list.map(function (id) { return "car=" + encodeURIComponent(id); }).join("&");
  }

  function bindCompare(root) {
    (root || document).querySelectorAll("[data-compare-toggle]").forEach(function (btn) {
      var publicId = btn.getAttribute("data-compare-toggle");
      if (compareList().indexOf(publicId) !== -1) btn.classList.add("is-active");
      if (btn.__bound) return;
      btn.__bound = true;
      btn.addEventListener("click", function (evt) {
        evt.preventDefault();
        var current = compareList();
        var idx = current.indexOf(publicId);
        if (idx === -1) {
          if (current.length >= 3) { toast("Можно сравнить не более 3 автомобилей", "error"); return; }
          current.push(publicId);
          btn.classList.add("is-active");
        } else {
          current.splice(idx, 1);
          btn.classList.remove("is-active");
        }
        try { sessionStorage.setItem(COMPARE_KEY, JSON.stringify(current)); } catch (e) { /* ignore */ }
        renderCompareBar();
      });
    });
  }
  bindCompare();
  renderCompareBar();

  /* --------------------------- «Показать ещё» в каталоге --------------------- */

  var moreBtn = document.querySelector("[data-load-more]");
  if (moreBtn) {
    var grid = document.getElementById("catalog-grid");
    moreBtn.addEventListener("click", function (evt) {
      evt.preventDefault();
      var url = moreBtn.getAttribute("data-next-url") || moreBtn.getAttribute("href");
      moreBtn.disabled = true;
      moreBtn.textContent = "Загружаем…";
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" } })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          grid.insertAdjacentHTML("beforeend", data.html);
          markFavorites(grid);
          bindCompare(grid);
          if (data.has_more) {
            moreBtn.setAttribute("data-next-url", data.next_url);
            moreBtn.setAttribute("href", data.next_url);
            moreBtn.disabled = false;
            moreBtn.textContent = "Показать ещё";
          } else {
            moreBtn.parentNode.remove();
          }
        })
        .catch(function () {
          moreBtn.disabled = false;
          moreBtn.textContent = "Показать ещё";
          toast("Не удалось загрузить. Попробуйте ещё раз.", "error");
        });
    });
  }

  /* --------------------------- Слайдер бюджета -------------------------------- */

  var budgetSlider = document.getElementById("budget-range");
  if (budgetSlider) {
    var display = document.getElementById("budget-value");
    var update = function () {
      var value = parseInt(budgetSlider.value, 10);
      display.textContent = value.toLocaleString("ru-RU") + " ₽ / сутки";
    };
    budgetSlider.addEventListener("input", update);
    update();
  }

  /* ------------------------------ Галерея карточки ---------------------------- */

  document.querySelectorAll("[data-gallery-thumb]").forEach(function (thumb) {
    thumb.addEventListener("click", function () {
      var gallery = thumb.closest("[data-gallery]");
      var mainImg = gallery.querySelector("[data-gallery-main]");
      mainImg.src = thumb.getAttribute("data-gallery-thumb");
      gallery.querySelectorAll("[data-gallery-thumb]").forEach(function (t) { t.classList.remove("is-active"); });
      thumb.classList.add("is-active");
    });
  });

  /* ------------------------- Фото: загрузка, порядок, удаление ---------------- */

  function photoItem(src, token, isPrimary) {
    var wrap = document.createElement("div");
    wrap.className = "photo-item" + (isPrimary ? " is-primary" : "");
    wrap.setAttribute("data-photo-item", "");
    wrap.setAttribute("data-token", token);
    var img = document.createElement("img");
    img.src = src;
    img.alt = "";
    wrap.appendChild(img);
    wrap.insertAdjacentHTML("beforeend",
      '<span class="photo-item__badge">Главное</span>' +
      '<button type="button" class="photo-item__btn photo-item__primary" data-photo-primary aria-label="Сделать главным">★</button>' +
      '<button type="button" class="photo-item__btn photo-item__del" data-photo-del aria-label="Удалить фото">×</button>');
    return wrap;
  }

  function refreshPhotoGrid(grid) {
    var items = grid.querySelectorAll("[data-photo-item]");
    items.forEach(function (it, i) { it.classList.toggle("is-primary", i === 0); });
    var counter = document.querySelector("[data-photo-count]");
    if (counter) counter.textContent = items.length;
  }

  function photoUrl(template, token) { return template.replace("{token}", encodeURIComponent(token)); }

  document.querySelectorAll("[data-photo-upload]").forEach(function (input) {
    var endpoint = input.getAttribute("data-photo-upload");
    var grid = document.getElementById(input.getAttribute("data-preview-target"));
    var maxPhotos = parseInt(input.getAttribute("data-max-photos") || "0", 10);

    input.addEventListener("change", function () {
      Array.prototype.forEach.call(input.files, function (file) {
        if (maxPhotos && grid.querySelectorAll("[data-photo-item]").length >= maxPhotos) {
          toast("Достигнут лимит фотографий на вашем тарифе", "error");
          return;
        }
        var formData = new FormData();
        formData.append("photo", file);

        var placeholder = document.createElement("div");
        placeholder.className = "photo-item skeleton";
        grid.appendChild(placeholder);

        request(endpoint, { body: formData })
          .then(function (data) {
            if (data.error || !data._ok) {
              toast(data.error || "Не удалось загрузить фото", "error");
              placeholder.remove();
              return;
            }
            placeholder.replaceWith(photoItem(data.thumbnail_url || data.url, data.token, false));
            refreshPhotoGrid(grid);
          })
          .catch(function () {
            toast("Не удалось загрузить фото. Попробуйте ещё раз.", "error");
            placeholder.remove();
          });
      });
      input.value = "";
    });
  });

  document.querySelectorAll("[data-photo-grid]").forEach(function (grid) {
    var delUrl = grid.getAttribute("data-delete-url");
    var primaryUrl = grid.getAttribute("data-primary-url");

    grid.addEventListener("click", function (evt) {
      var item = evt.target.closest("[data-photo-item]");
      if (!item) return;
      var token = item.getAttribute("data-token");

      if (evt.target.closest("[data-photo-del]")) {
        if (!window.confirm("Удалить это фото?")) return;
        var url = photoUrl(delUrl, token);
        var opts = delUrl.indexOf("{token}") === -1
          ? { body: new URLSearchParams({ token: token }).toString(), headers: { "Content-Type": "application/x-www-form-urlencoded" } }
          : {};
        request(url, opts).then(function (data) {
          if (!data._ok) { toast(data.error || "Не удалось удалить фото", "error"); return; }
          item.remove();
          refreshPhotoGrid(grid);
        });
      } else if (evt.target.closest("[data-photo-primary]")) {
        var pUrl = photoUrl(primaryUrl, token);
        var pOpts = primaryUrl.indexOf("{token}") === -1
          ? { body: new URLSearchParams({ token: token }).toString(), headers: { "Content-Type": "application/x-www-form-urlencoded" } }
          : {};
        request(pUrl, pOpts).then(function (data) {
          if (!data._ok) { toast(data.error || "Не удалось изменить порядок", "error"); return; }
          grid.insertBefore(item, grid.firstChild);
          refreshPhotoGrid(grid);
          toast("Главное фото обновлено", "success");
        });
      }
    });
  });

  /* ------------------------------ Подтвердить актуальность --------------------- */

  document.querySelectorAll("[data-confirm-actual]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      postForm(btn.getAttribute("data-confirm-actual"), {}).then(function (data) {
        if (data.ok) {
          btn.textContent = "Подтверждено сегодня ✓";
          btn.disabled = true;
        } else {
          toast("Не удалось подтвердить. Обновите страницу.", "error");
        }
      });
    });
  });

  /* ------------------------------ Автопереход на оплату ------------------------ */

  var auto = document.querySelector("[data-autoredirect]");
  if (auto) {
    var target = auto.getAttribute("data-autoredirect");
    if (/^https:\/\//.test(target)) setTimeout(function () { window.location.href = target; }, 1500);
  }

  /* --------------------- Приложение: service worker и кнопка «Установить» ---------- */

  if ("serviceWorker" in navigator && window.location.protocol === "https:") {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* без него сайт тоже работает */ });
    });
  }

  var installPrompt = null;
  function toggleInstall(show) {
    document.querySelectorAll("[data-install]").forEach(function (b) { b.hidden = !show; });
  }
  window.addEventListener("beforeinstallprompt", function (evt) {
    evt.preventDefault();
    installPrompt = evt;
    toggleInstall(true);
  });
  window.addEventListener("appinstalled", function () { installPrompt = null; toggleInstall(false); });
  document.querySelectorAll("[data-install]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!installPrompt) return;
      installPrompt.prompt();
      installPrompt.userChoice.then(function () { installPrompt = null; toggleInstall(false); });
    });
  });
})();
