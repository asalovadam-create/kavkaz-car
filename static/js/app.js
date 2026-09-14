/*
 * KAVKAZ-CAR — лёгкий клиентский JS без тяжёлых библиотек (раздел 58 ТЗ).
 * Каждый блок работает независимо и ничего не делает, если на странице
 * нет соответствующей разметки.
 */
(function () {
  "use strict";

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF_TOKEN = csrfMeta ? csrfMeta.content : "";

  function postForm(url, data) {
    var formData = new URLSearchParams(data || {});
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRF-Token": CSRF_TOKEN,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: formData.toString(),
    }).then(function (res) {
      return res.json();
    });
  }

  /* ------------------------- Bottom sheet (фильтры) ------------------------- */
  document.querySelectorAll("[data-sheet-open]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sheet = document.getElementById(btn.getAttribute("data-sheet-open"));
      var backdrop = document.querySelector(".sheet-backdrop");
      if (!sheet) return;
      sheet.classList.add("is-open");
      if (backdrop) backdrop.classList.add("is-open");
      document.body.style.overflow = "hidden";
    });
  });
  document.querySelectorAll("[data-sheet-close]").forEach(function (btn) {
    btn.addEventListener("click", closeSheets);
  });
  var backdropEl = document.querySelector(".sheet-backdrop");
  if (backdropEl) backdropEl.addEventListener("click", closeSheets);

  function closeSheets() {
    document.querySelectorAll(".bottom-sheet.is-open").forEach(function (s) {
      s.classList.remove("is-open");
    });
    if (backdropEl) backdropEl.classList.remove("is-open");
    document.body.style.overflow = "";
  }

  /* ------------------------------ Избранное --------------------------------- */
  var FAV_KEY = "kavkazcar_favorites";
  var isAuthenticated = document.body.getAttribute("data-authenticated") === "1";

  function localFavorites() {
    try {
      return JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function setLocalFavorite(publicId, isFav) {
    var list = localFavorites();
    var idx = list.indexOf(publicId);
    if (isFav && idx === -1) list.push(publicId);
    if (!isFav && idx !== -1) list.splice(idx, 1);
    localStorage.setItem(FAV_KEY, JSON.stringify(list));
  }

  document.querySelectorAll("[data-fav-toggle]").forEach(function (btn) {
    var publicId = btn.getAttribute("data-fav-toggle");
    if (!isAuthenticated && localFavorites().indexOf(publicId) !== -1) {
      btn.classList.add("is-active");
    }
    btn.addEventListener("click", function (evt) {
      evt.preventDefault();
      evt.stopPropagation();

      if (!isAuthenticated) {
        var nowActive = !btn.classList.contains("is-active");
        btn.classList.toggle("is-active", nowActive);
        setLocalFavorite(publicId, nowActive);
        return;
      }

      postForm("/favorites/toggle/" + publicId, {}).then(function (data) {
        btn.classList.toggle("is-active", !!data.favorite);
      });
    });
  });

  /* ------------------------------ Сравнение ---------------------------------- */
  var COMPARE_KEY = "kavkazcar_compare";

  function compareList() {
    try {
      return JSON.parse(sessionStorage.getItem(COMPARE_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function renderCompareBar() {
    var bar = document.getElementById("compare-bar");
    if (!bar) return;
    var list = compareList();
    if (list.length === 0) {
      bar.style.display = "none";
      return;
    }
    bar.style.display = "flex";
    bar.querySelector("[data-compare-count]").textContent = list.length;
    bar.querySelector("[data-compare-link]").href =
      "/compare?" + list.map(function (id) { return "car=" + encodeURIComponent(id); }).join("&");
  }

  document.querySelectorAll("[data-compare-toggle]").forEach(function (btn) {
    var publicId = btn.getAttribute("data-compare-toggle");
    var list = compareList();
    if (list.indexOf(publicId) !== -1) btn.classList.add("is-active");

    btn.addEventListener("click", function (evt) {
      evt.preventDefault();
      var current = compareList();
      var idx = current.indexOf(publicId);
      if (idx === -1) {
        if (current.length >= 3) {
          alert("Можно сравнить не более 3 автомобилей.");
          return;
        }
        current.push(publicId);
        btn.classList.add("is-active");
      } else {
        current.splice(idx, 1);
        btn.classList.remove("is-active");
      }
      sessionStorage.setItem(COMPARE_KEY, JSON.stringify(current));
      renderCompareBar();
    });
  });
  renderCompareBar();

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
      gallery.querySelectorAll("[data-gallery-thumb]").forEach(function (t) {
        t.classList.remove("is-active");
      });
      thumb.classList.add("is-active");
    });
  });

  /* ------------------------- Загрузка фото (мастер/владелец) ------------------ */
  document.querySelectorAll("[data-photo-upload]").forEach(function (input) {
    var endpoint = input.getAttribute("data-photo-upload");
    var grid = document.getElementById(input.getAttribute("data-preview-target"));

    input.addEventListener("change", function () {
      Array.prototype.forEach.call(input.files, function (file) {
        var formData = new FormData();
        formData.append("photo", file);

        var placeholder = document.createElement("div");
        placeholder.className = "photo-preview-grid__item skeleton";
        placeholder.style.aspectRatio = "1";
        placeholder.style.borderRadius = "10px";
        if (grid) grid.appendChild(placeholder);

        fetch(endpoint, {
          method: "POST",
          headers: { "X-CSRF-Token": CSRF_TOKEN },
          body: formData,
        })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            if (data.error) {
              alert(data.error);
              placeholder.remove();
              return;
            }
            var img = document.createElement("img");
            img.src = data.thumbnail_url || data.url;
            if (grid && grid.children.length === 1) img.classList.add("is-primary");
            placeholder.replaceWith(img);
          })
          .catch(function () {
            alert("Не удалось загрузить фото. Попробуйте ещё раз.");
            placeholder.remove();
          });
      });
      input.value = "";
    });
  });

  /* ------------------------------ Подтвердить актуальность --------------------- */
  document.querySelectorAll("[data-confirm-actual]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-confirm-actual");
      postForm(url, {}).then(function (data) {
        if (data.ok) {
          btn.textContent = "Актуально сегодня ✓";
          btn.disabled = true;
        }
      });
    });
  });

  /* --------------------------------- Мобильное меню ----------------------------- */
  var menuBtn = document.getElementById("mobile-menu-btn");
  var mobileMenu = document.getElementById("mobile-menu");
  if (menuBtn && mobileMenu) {
    menuBtn.addEventListener("click", function () {
      mobileMenu.classList.toggle("is-open");
    });
  }
})();
