<script>
  import { marked } from 'marked'
  import DOMPurify from 'dompurify'
  import { get, post, tierBadge } from '../api.js'

  let { id, onselect, onchanged } = $props()

  let detail = $state(null)
  let error = $state('')
  let newTier = $state('')
  let busy = $state(false)
  let editing = $state(false)
  let previewing = $state(false)
  let draftBody = $state('')

  function renderMarkdown(text) {
    return DOMPurify.sanitize(marked.parse(text ?? ''))
  }
  let trust = $state(0)
  let retrieval = $state(0)
  let edgeType = $state('DEPENDS_ON')
  let edgeDirection = $state('out')
  let edgeTarget = $state('')

  async function load() {
    try {
      detail = await get(`/api/nodes/${id}`)
      newTier = detail.node.tier
      trust = detail.node.trust_weight
      retrieval = detail.node.retrieval_weight
      editing = false
      previewing = false
      error = ''
    } catch (e) {
      error = e.message
      detail = null
    }
  }

  $effect(() => {
    id
    load()
  })

  async function act(fn) {
    busy = true
    error = ''
    try {
      await fn()
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  const clearFlag = () =>
    act(() => post(`/api/nodes/${id}/clear-flag`, { reason: 'reviewed in GUI: false alarm' }))

  const recomputeTrust = () => act(() => post(`/api/nodes/${id}/recompute-trust`))

  function applyTier() {
    const confirmed =
      newTier !== 'lifetime' ||
      window.confirm(
        'Promote to LIFETIME? Lifetime lessons are always in context and unconditionally trusted — this is the mandatory human checkpoint.'
      )
    if (!confirmed) return
    act(() => post(`/api/nodes/${id}/tier`, { tier: newTier, confirmed: true }))
  }

  const saveBody = () =>
    act(() => post(`/api/nodes/${id}/body`, { body: draftBody, reason: 'edited in GUI' }))

  const saveWeights = () =>
    act(() =>
      post(`/api/nodes/${id}/weights`, {
        trust_weight: Number(trust),
        retrieval_weight: Number(retrieval),
        reason: 'set in GUI',
      })
    )

  const toggleArchived = () =>
    act(() => post(`/api/nodes/${id}/archived`, { archived: !detail.node.archived }))

  const addEdge = () =>
    act(async () => {
      const [source, target] =
        edgeDirection === 'out' ? [id, edgeTarget.trim()] : [edgeTarget.trim(), id]
      await post('/api/edges', { source, target, type: edgeType })
      edgeTarget = ''
    })
</script>

{#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

{#if detail}
  {@const n = detail.node}
  <div class="card">
    <div class="card-header d-flex justify-content-between align-items-center flex-wrap gap-2">
      <code>{n.path}</code>
      <span>
        <span class="badge text-bg-light border">{n.type}</span>
        <span class="badge {tierBadge[n.tier]}">{n.tier}</span>
        {#if n.needs_review}<span class="badge text-bg-danger">disputed</span>{/if}
        {#if n.archived}<span class="badge text-bg-warning">archived</span>{/if}
      </span>
    </div>
    <div class="card-body">
      {#if editing}
        <div class="btn-group btn-group-sm mb-2" role="group">
          <button
            type="button"
            class="btn {previewing ? 'btn-outline-secondary' : 'btn-secondary'}"
            onclick={() => (previewing = false)}
          >
            edit
          </button>
          <button
            type="button"
            class="btn {previewing ? 'btn-secondary' : 'btn-outline-secondary'}"
            onclick={() => (previewing = true)}
          >
            preview
          </button>
        </div>
        {#if previewing}
          <div class="markdown-body border rounded p-2 mb-2">{@html renderMarkdown(draftBody)}</div>
        {:else}
          <textarea class="form-control mb-2" rows="5" bind:value={draftBody}></textarea>
        {/if}
        <div class="d-flex gap-2 mb-3">
          <button
            class="btn btn-sm btn-primary"
            onclick={saveBody}
            disabled={busy || !draftBody.trim()}
          >
            save body
          </button>
          <button class="btn btn-sm btn-outline-secondary" onclick={() => (editing = false)}>
            cancel
          </button>
          <span class="small text-secondary align-self-center">
            journaled as content_edited — re-confirm or recompute trust if prior
            confirmations no longer apply
          </span>
        </div>
      {:else}
        <div class="markdown-body mb-2">{@html renderMarkdown(n.body)}</div>
      {/if}
      <div class="small text-secondary mb-3">
        id <code>{n.id}</code> · trust {n.trust_weight.toFixed(2)} · retrieval
        {n.retrieval_weight.toFixed(2)} · created {n.created_at?.slice(0, 16) ?? '—'}
        {#if !editing}
          · <button
            class="btn btn-link btn-sm p-0 align-baseline"
            onclick={() => {
              draftBody = n.body
              previewing = false
              editing = true
            }}
          >
            edit body
          </button>
        {/if}
      </div>

      <div class="d-flex gap-2 flex-wrap align-items-center border-top pt-3">
        {#if n.needs_review}
          <button class="btn btn-sm btn-outline-success" onclick={clearFlag} disabled={busy}>
            clear flag (false alarm)
          </button>
          {#if detail.resolver_verdict}
            <span class="small text-secondary">
              rules verdict: <strong>{detail.resolver_verdict}</strong>
            </span>
          {/if}
        {/if}
        <button class="btn btn-sm btn-outline-secondary" onclick={recomputeTrust} disabled={busy}>
          recompute trust from journal
        </button>
        <span class="ms-auto d-flex gap-1 align-items-center">
          <select class="form-select form-select-sm w-auto" bind:value={newTier}>
            {#each ['short-term', 'mid-term', 'long-term', 'lifetime'] as t}
              <option value={t}>{t}</option>
            {/each}
          </select>
          <button
            class="btn btn-sm btn-outline-primary"
            onclick={applyTier}
            disabled={busy || newTier === n.tier}
          >
            set tier
          </button>
        </span>
      </div>

      <div class="d-flex gap-2 flex-wrap align-items-center border-top pt-3 mt-3 small">
        <label class="text-secondary" for="trust-w">trust</label>
        <input
          id="trust-w"
          class="form-control form-control-sm"
          style="width: 5.5rem"
          type="number"
          step="0.1"
          bind:value={trust}
        />
        <label class="text-secondary" for="retr-w">retrieval</label>
        <input
          id="retr-w"
          class="form-control form-control-sm"
          style="width: 5.5rem"
          type="number"
          step="0.1"
          bind:value={retrieval}
        />
        <button class="btn btn-sm btn-outline-primary" onclick={saveWeights} disabled={busy}>
          set weights
        </button>
        <span class="text-secondary">
          (manual trust holds until the next recompute folds the journal)
        </span>
        <button
          class="btn btn-sm {n.archived ? 'btn-outline-success' : 'btn-outline-warning'} ms-auto"
          onclick={toggleArchived}
          disabled={busy}
        >
          {n.archived ? 'unarchive' : 'archive'}
        </button>
      </div>
    </div>
  </div>

  <div class="row g-3 mt-0">
    {#each [['outgoing', detail.outgoing], ['incoming', detail.incoming]] as [label, edges]}
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-header py-1 small fw-semibold">{label} edges ({edges.length})</div>
          <ul class="list-group list-group-flush">
            {#each edges as e}
              <li class="list-group-item py-1 d-flex align-items-center gap-2">
                <span class="badge text-bg-light border">{e.edge_type}</span>
                <button
                  class="btn btn-link btn-sm p-0 text-truncate"
                  onclick={() => onselect?.(e.node.id)}
                  title={e.node.preview}
                >
                  {e.node.path}
                </button>
              </li>
            {:else}
              <li class="list-group-item py-1 text-secondary small">none</li>
            {/each}
          </ul>
        </div>
      </div>
    {/each}
  </div>

  <div class="card mt-3">
    <div class="card-header py-1 small fw-semibold">add edge</div>
    <div class="card-body py-2 d-flex gap-2 flex-wrap align-items-center">
      <select class="form-select form-select-sm w-auto" bind:value={edgeDirection}>
        <option value="out">this node →</option>
        <option value="in">→ this node</option>
      </select>
      <select class="form-select form-select-sm w-auto" bind:value={edgeType}>
        <option value="DEPENDS_ON">DEPENDS_ON</option>
        <option value="CONTRADICTS">CONTRADICTS</option>
      </select>
      <input
        class="form-control form-control-sm flex-grow-1"
        placeholder="target node id (copy from another node's detail)"
        bind:value={edgeTarget}
      />
      <button
        class="btn btn-sm btn-outline-primary"
        onclick={addEdge}
        disabled={busy || !edgeTarget.trim()}
      >
        add
      </button>
      <span class="small text-secondary w-100">
        CONTRADICTS flags the target for review as a journaled side-effect.
      </span>
    </div>
  </div>

  <div class="card mt-3">
    <div class="card-header py-1 small fw-semibold">journal ({detail.events.length})</div>
    <div class="table-responsive" style="max-height: 30vh">
      <table class="table table-sm mb-0 small">
        <tbody>
          {#each detail.events.toReversed() as e}
            <tr>
              <td class="text-nowrap text-secondary">{e.created_at?.slice(0, 16)}</td>
              <td><span class="badge text-bg-light border">{e.type}</span></td>
              <td class="text-nowrap">{e.polarity > 0 ? '+' : '−'}{e.weight}</td>
              <td class="text-secondary">{e.source}</td>
              <td>{e.reason}</td>
            </tr>
          {:else}
            <tr><td class="text-secondary">no events</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
{/if}

<style>
  /* Rendered markdown fits the card layout. `:global` is needed because the HTML
     is injected via {@html} and would otherwise escape Svelte's scoping. */
  .markdown-body :global(> :first-child) {
    margin-top: 0;
  }
  .markdown-body :global(> :last-child) {
    margin-bottom: 0;
  }
  .markdown-body :global(h1) {
    font-size: 1.35rem;
  }
  .markdown-body :global(h2) {
    font-size: 1.2rem;
  }
  .markdown-body :global(h3) {
    font-size: 1.1rem;
  }
  .markdown-body :global(h4),
  .markdown-body :global(h5),
  .markdown-body :global(h6) {
    font-size: 1rem;
  }
  .markdown-body :global(pre) {
    background: var(--bs-tertiary-bg);
    padding: 0.6rem 0.75rem;
    border-radius: 0.375rem;
    overflow-x: auto;
  }
  .markdown-body :global(:not(pre) > code) {
    background: var(--bs-tertiary-bg);
    padding: 0.1rem 0.3rem;
    border-radius: 0.25rem;
  }
  .markdown-body :global(blockquote) {
    border-left: 0.25rem solid var(--bs-border-color);
    padding-left: 0.75rem;
    color: var(--bs-secondary-color);
    margin-left: 0;
  }
  .markdown-body :global(table) {
    border-collapse: collapse;
  }
  .markdown-body :global(th),
  .markdown-body :global(td) {
    border: 1px solid var(--bs-border-color);
    padding: 0.25rem 0.5rem;
  }
  .markdown-body :global(img) {
    max-width: 100%;
  }
</style>
