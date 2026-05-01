// ==UserScript==
// @name         SlicerBridge
// @namespace    https://github.com/LukysGaming/SlicerBridge
// @version      2
// @description  Adds "Open in Slicer" button next to each folder on Printables model pages
// @author       LukysGaming
// @match        https://www.printables.com/model/*
// @grant        none
// @run-at       document-idle
// @license      MPL 2.0
// @downloadURL https://update.greasyfork.org/scripts/576211/SlicerBridge.user.js
// @updateURL https://update.greasyfork.org/scripts/576211/SlicerBridge.meta.js
// ==/UserScript==

(function () {
    'use strict';

    const PROTOCOL       = 'slicerbridge';
    const PRINTABLES_API = 'https://api.printables.com/graphql/';
    const BTN_LABEL      = '⬡ Open in Slicer';
    const BTN_LABEL_ALL  = '⬡ Open ALL in Slicer';

    const BTN_STYLE = [
        'display:inline-flex',
        'align-items:center',
        'gap:5px',
        'padding:4px 10px',
        'font-size:12px',
        'font-family:inherit',
        'font-weight:600',
        'border:1px solid #7aa2f7',
        'border-radius:4px',
        'background:transparent',
        'color:#7aa2f7',
        'cursor:pointer',
        'transition:background 0.15s,color 0.15s',
        'white-space:nowrap',
        'line-height:1.4',
        'vertical-align:middle',
        'margin-left:8px',
        'flex-shrink:0',
    ].join(';');

    function getModelId() {
        const m = location.pathname.match(/\/model\/(\d+)/);
        return m ? m[1] : null;
    }

    // ── Fetch list of STL files for this model ────────────────────────────────

    async function fetchModelFiles(modelId) {
        const query = `
          query ModelFiles($id: ID!) {
            model: print(id: $id) {
              stls { id name fileSize folder }
            }
          }
        `;
        try {
            const resp = await fetch(PRINTABLES_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    operationName: 'ModelFiles',
                    query,
                    variables: { id: modelId },
                }),
            });
            const json = await resp.json();
            if (json?.errors) console.warn('[SlicerBridge] GraphQL errors:', json.errors);
            return json?.data?.model?.stls ?? [];
        } catch (e) {
            console.warn('[SlicerBridge] fetchModelFiles failed:', e);
            return [];
        }
    }

    // ── Resolve real download URL via GetDownloadLink mutation ────────────────
    // Must run in the browser context so browser cookies are sent automatically
    // (credentials: 'include'). Printables requires this for download links.

    const DOWNLOAD_MUTATION = `
      mutation GetDownloadLink($id: ID!, $modelId: ID!, $fileType: DownloadFileTypeEnum!, $source: DownloadSourceEnum!) {
        getDownloadLink(id: $id, printId: $modelId, fileType: $fileType, source: $source) {
          ok
          errors { field messages __typename }
          output { link count ttl __typename }
          __typename
        }
      }
    `;

    async function resolveDownloadUrl(stlId, modelId) {
        try {
            const resp = await fetch(PRINTABLES_API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    operationName: 'GetDownloadLink',
                    query: DOWNLOAD_MUTATION,
                    variables: {
                        id: String(stlId),
                        modelId: String(modelId),
                        fileType: 'stl',
                        source: 'model_detail',
                    },
                }),
            });
            const json = await resp.json();
            const link = json?.data?.getDownloadLink?.output?.link;
            if (!link) console.warn(`[SlicerBridge] No link for stl ${stlId}:`, json);
            return link ?? null;
        } catch (e) {
            console.warn(`[SlicerBridge] resolveDownloadUrl failed for ${stlId}:`, e);
            return null;
        }
    }

    // ── Build slicerbridge:// URI — resolves all URLs first ──────────────────

    async function buildMultiUri(stls, modelId) {
        console.log(`[SlicerBridge] Resolving ${stls.length} download URL(s)...`);

        const resolved = await Promise.all(
            stls.map(s => resolveDownloadUrl(s.id, modelId))
        );

        const urls = [], names = [];
        for (let i = 0; i < stls.length; i++) {
            if (resolved[i]) {
                urls.push(resolved[i]);
                names.push(stls[i].name);
            } else {
                console.warn(`[SlicerBridge] Skipping ${stls[i].name} — no URL`);
            }
        }

        if (!urls.length) return null;
        return `${PROTOCOL}://multi?files=${encodeURIComponent(urls.join('|'))}&names=${encodeURIComponent(names.join('|'))}`;
    }

    // ── Button factory ────────────────────────────────────────────────────────

    function makeButton(label, onClickAsync) {
        const btn = document.createElement('button');
        btn.textContent = label;
        btn.setAttribute('style', BTN_STYLE);
        btn.addEventListener('mouseenter', () => {
            btn.style.background = '#7aa2f7';
            btn.style.color = '#16161e';
        });
        btn.addEventListener('mouseleave', () => {
            btn.style.background = 'transparent';
            btn.style.color = '#7aa2f7';
        });
        btn.addEventListener('click', async e => {
            e.preventDefault();
            e.stopPropagation();
            btn.textContent = '⏳ Resolving...';
            btn.disabled = true;
            try {
                await onClickAsync();
            } finally {
                btn.textContent = label;
                btn.disabled = false;
            }
        });
        return btn;
    }

    // ── DOM helpers ───────────────────────────────────────────────────────────

    function normalizeName(str) {
        return (str || '').trim().replace(/\s+/g, ' ');
    }

    function getFolderNameFromDataHref(folderItem) {
        const href = folderItem?.dataset?.href || '';
        const m = href.match(/#folder:[^:]+:(.+)/);
        if (m) return decodeURIComponent(m[1]);
        return null;
    }

    function findFolderHeaderInfos() {
        const folderItems = [...document.querySelectorAll('.folder-item')];
        if (folderItems.length) {
            const results = folderItems
                .map(item => {
                    const header = item.querySelector('header');
                    if (!header) return null;
                    const nameEl = header.querySelector('.folder-name, [class*="folder-name"]');
                    const nameFromEl = nameEl ? normalizeName(nameEl.textContent) : null;
                    const nameFromHref = normalizeName(getFolderNameFromDataHref(item));
                    return { header, folderName: nameFromEl || nameFromHref };
                })
                .filter(Boolean);
            if (results.length) return results;
        }

        const ariaHeaders = [...document.querySelectorAll('header[aria-label*="folder" i]')];
        return ariaHeaders.map(header => {
            const nameEl = header.querySelector('.folder-name, [class*="folder-name"]');
            const nameFromEl = nameEl ? normalizeName(nameEl.textContent) : null;
            const ariaMatch = (header.getAttribute('aria-label') || '').match(/folder\s+(.+)/i);
            const nameFromAria = ariaMatch ? normalizeName(ariaMatch[1]) : null;
            return { header, folderName: nameFromEl || nameFromAria };
        });
    }

    function findDownloadAllArea() {
        return (
            document.querySelector('[data-testid="download-all-model"]')?.parentElement ||
            document.querySelector('.section-header .flex') ||
            document.querySelector('[class*="section-header"] [class*="flex"]')
        );
    }

    // ── Inject buttons into DOM ───────────────────────────────────────────────

    function inject(allStls, modelId) {
        if (!allStls.length) return;

        const byFolder = {};
        for (const stl of allStls) {
            const key = normalizeName(stl.folder) || '__root__';
            if (!byFolder[key]) byFolder[key] = [];
            byFolder[key].push(stl);
        }

        console.log('[SlicerBridge] API folders:', Object.keys(byFolder));

        const folderHeaderInfos = findFolderHeaderInfos();
        if (!folderHeaderInfos.length) return;

        for (const { header, folderName } of folderHeaderInfos) {
            if (header.querySelector('.sb-open-btn')) continue;
            if (!folderName) continue;

            let stls = byFolder[folderName];
            if (!stls) {
                const apiKey = Object.keys(byFolder).find(
                    k => k.toLowerCase() === folderName.toLowerCase()
                );
                if (apiKey) stls = byFolder[apiKey];
            }

            if (!stls?.length) {
                console.log(`[SlicerBridge] No match for folder: "${folderName}". API has:`, Object.keys(byFolder));
                continue;
            }

            const btn = makeButton(BTN_LABEL, async () => {
                const uri = await buildMultiUri(stls, modelId);
                if (uri) location.href = uri;
                else alert('[SlicerBridge] Could not resolve download URLs.\nAre you logged in to Printables?');
            });
            btn.classList.add('sb-open-btn');
            btn.title = `Open ${stls.length} file(s) in your slicer via SlicerBridge`;

            const sizeEl = header.querySelector('.folder-size, [class*="folder-size"]');
            const nameEl = header.querySelector('.folder-name, [class*="folder-name"]');

            if (sizeEl) header.insertBefore(btn, sizeEl);
            else if (nameEl) nameEl.after(btn);
            else header.appendChild(btn);

            console.log(`[SlicerBridge] Injected button for: "${folderName}" (${stls.length} files)`);
        }

        // "Open ALL" button
        const dlArea = findDownloadAllArea();
        if (dlArea && !dlArea.querySelector('.sb-open-all-btn')) {
            const allBtn = makeButton(BTN_LABEL_ALL, async () => {
                const uri = await buildMultiUri(allStls, modelId);
                if (uri) location.href = uri;
                else alert('[SlicerBridge] Could not resolve download URLs.\nAre you logged in to Printables?');
            });
            allBtn.classList.add('sb-open-all-btn');
            allBtn.title = `Open all ${allStls.length} files in your slicer via SlicerBridge`;
            dlArea.appendChild(allBtn);
            console.log(`[SlicerBridge] Injected Open ALL button (${allStls.length} files)`);
        }
    }

    // ── Entry point ───────────────────────────────────────────────────────────

    async function start(modelId) {
        const allStls = await fetchModelFiles(modelId);
        if (!allStls.length) {
            console.log('[SlicerBridge] No STL files found for model', modelId);
            return;
        }
        console.log(`[SlicerBridge] Fetched ${allStls.length} STL file(s)`);

        inject(allStls, modelId);

        let debounceTimer = null;
        const obs = new MutationObserver(() => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => inject(allStls, modelId), 500);
        });
        obs.observe(document.body, { childList: true, subtree: true });

        setTimeout(() => {
            obs.disconnect();
            clearTimeout(debounceTimer);
        }, 30_000);
    }

    const modelId = getModelId();
    if (modelId) {
        if (document.readyState === 'complete') {
            setTimeout(() => start(modelId), 800);
        } else {
            window.addEventListener('load', () => setTimeout(() => start(modelId), 800));
        }
    }

})();