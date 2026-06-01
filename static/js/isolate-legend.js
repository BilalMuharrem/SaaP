/* ════════════════════════════════════════════════════════════════════════
   isolate-legend.js — ApexCharts legend "İzole Et ve Kıyasla" davranışı
   ────────────────────────────────────────────────────────────────────────
   HOTFIX 10.4: Standart ApexCharts lejant tıklaması bir seriyi gizleyip
   gösteriyor (klasik toggle). E-ticaret kıyaslama için bu kötü UX — kullanıcı
   rakipleri birbirine karşı görmek isterse, önce N-1 seriyi tek tek
   kapatması gerek.

   Yeni davranış:
     • Tüm seriler görünürken bir isime tıklama → o seri AÇIK kalır, DİĞERLERİ
       gizlenir (anında o ürüne odaklan).
     • İzole moddayken gizli bir isime tıklama → o seri eklenir (kıyaslama).
     • İzole moddayken görünür bir isime tıklama → klasik toggle (gizle).

   Kullanım:
     var options = { ... };  // ApexCharts options
     window.applyIsolateLegendBehavior(options);
     var chart = new ApexCharts(el, options);
     chart.render();

   Helper, options.legend.onItemClick.toggleDataSeries'i KAPATIR (default
   toggle devre dışı) ve options.chart.events.legendClick'i kendi mantığıyla
   doldurur. Var olan legendClick handler'ları korunur (sonra çağrılır).
   ════════════════════════════════════════════════════════════════════════ */
(function() {
    'use strict';

    function applyIsolateLegendBehavior(options) {
        if (!options || typeof options !== 'object') return options;

        // 1) ApexCharts'ın default toggle'ını kapat — kontrol bizde
        options.legend = options.legend || {};
        options.legend.onItemClick = options.legend.onItemClick || {};
        options.legend.onItemClick.toggleDataSeries = false;

        // 2) Var olan custom legendClick handler'ını koru, sonrasında çağırırız
        options.chart = options.chart || {};
        options.chart.events = options.chart.events || {};
        var previousHandler = options.chart.events.legendClick;

        options.chart.events.legendClick = function(chartContext, seriesIndex, config) {
            try {
                // Series listesi — config.config.series (canonical) veya
                // chartContext.w.config.series (render sonrası) — ikisini de dene
                var series = (config && config.config && config.config.series) ||
                             (chartContext && chartContext.w && chartContext.w.config && chartContext.w.config.series) ||
                             [];
                if (!series.length || seriesIndex == null || seriesIndex < 0 || seriesIndex >= series.length) {
                    return;
                }

                var clickedName = series[seriesIndex].name;
                if (!clickedName) return;

                // Gizli seri index'leri — ApexCharts internal state
                var hidden = (chartContext.w && chartContext.w.globals &&
                              chartContext.w.globals.collapsedSeriesIndices) || [];
                var visibleCount = series.length - hidden.length;
                var clickedIsHidden = hidden.indexOf(seriesIndex) !== -1;
                var allVisible = (hidden.length === 0);

                if (allVisible) {
                    // ── Durum 1: Tümü görünürken bir isime tıklama → İZOLE ET ──
                    // Tıklananı kapat, diğerlerini gizle.
                    series.forEach(function(s, i) {
                        if (i !== seriesIndex && s.name) {
                            chartContext.hideSeries(s.name);
                        }
                    });
                } else if (clickedIsHidden) {
                    // ── Durum 2a: İzole modda gizli olana tıklama → GÖSTER (kıyaslama ekle) ──
                    chartContext.showSeries(clickedName);
                } else {
                    // ── Durum 2b: İzole modda görünür olana tıklama → KLASİK TOGGLE (gizle) ──
                    // Eğer bu son görünür seriyse, hepsini tekrar aç (UX kurtarma):
                    // kullanıcı yanlışlıkla ekranı tamamen boşaltmasın.
                    if (visibleCount <= 1) {
                        series.forEach(function(s) {
                            if (s.name) chartContext.showSeries(s.name);
                        });
                    } else {
                        chartContext.hideSeries(clickedName);
                    }
                }
            } catch (err) {
                // Tarayıcı konsolunda görünür, ApexCharts iç state değişikliği
                // gibi nadir senaryolarda graceful degrade
                if (window.console && console.warn) {
                    console.warn('[isolate-legend] hata:', err);
                }
            }

            // Önceden tanımlı handler varsa onu da çağır
            if (typeof previousHandler === 'function') {
                try { previousHandler.apply(this, arguments); } catch (e) {}
            }
        };

        return options;
    }

    // Global olarak yayınla — tüm grafik sayfaları kullanır
    window.applyIsolateLegendBehavior = applyIsolateLegendBehavior;
})();
