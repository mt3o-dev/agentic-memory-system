<script>
  import { get, tierBadge } from '../api.js'
  import NodeDetail from './NodeDetail.svelte'
  import CreateArtifact from './CreateArtifact.svelte'

  let { selectedId = $bindable(null), onchanged } = $props()

  let creating = $state(false)

  let q = $state('')
  let type = $state('')
  let tier = $state('')
  let flagged = $state(false)
  let archived = $state(false)
  let nodes = $state([])
  let error = $state('')

  async function load() {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (type) params.set('type', type)
    if (tier) params.set('tier', tier)
    if (flagged) params.set('flagged', '1')
    if (archived) params.set('archived', '1')
    try {
      nodes = await get(`/api/nodes?${params}`)
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  $effect(() => {
    q, type, tier, flagged, archived
    load()
  })
</script>

<div class="row g-3">
  <div class="col-lg-5">
    <div class="d-flex gap-2 mb-2 flex-wrap">
      <input
        class="form-control form-control-sm w-auto flex-grow-1"
        placeholder="search body, path, or id…"
        bind:value={q}
      />
      <select class="form-select form-select-sm w-auto" bind:value={type}>
        <option value="">any type</option>
        {#each ['decision', 'concept', 'constraint', 'issue', 'invariant', 'goal'] as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select class="form-select form-select-sm w-auto" bind:value={tier}>
        <option value="">any tier</option>
        {#each ['short-term', 'mid-term', 'long-term', 'lifetime'] as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <div class="form-check form-check-inline align-self-center">
        <input class="form-check-input" type="checkbox" id="flagged" bind:checked={flagged} />
        <label class="form-check-label small" for="flagged">flagged</label>
      </div>
      <div class="form-check form-check-inline align-self-center">
        <input class="form-check-input" type="checkbox" id="archived" bind:checked={archived} />
        <label class="form-check-label small" for="archived">incl. archived</label>
      </div>
      <button
        class="btn btn-sm btn-outline-primary ms-auto"
        onclick={() => (creating = true)}
      >
        ＋ artifact
      </button>
    </div>

    {#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

    <div class="list-group overflow-auto" style="max-height: 75vh">
      {#each nodes as node (node.id)}
        <button
          class="list-group-item list-group-item-action py-2 {selectedId === node.id
            ? 'active'
            : ''}"
          onclick={() => (selectedId = node.id)}
        >
          <div class="d-flex justify-content-between align-items-center">
            <code class="small {selectedId === node.id ? 'text-white' : ''}">{node.path}</code>
            <span>
              <span class="badge text-bg-light border">{node.type}</span>
              <span class="badge {tierBadge[node.tier]}">{node.tier}</span>
              {#if node.needs_review}<span class="badge text-bg-danger">disputed</span>{/if}
              {#if node.archived}<span class="badge text-bg-warning">archived</span>{/if}
            </span>
          </div>
          <div
            class="small {selectedId === node.id ? '' : 'text-secondary'}"
            style="display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;"
          >
            {node.preview}
          </div>
          <div
            class="text-truncate {selectedId === node.id ? 'text-white-50' : 'text-muted'}"
            style="font-size: 0.75em;"
          >
            id: <code class="small">{node.id}</code>
          </div>
        </button>
      {:else}
        <div class="text-secondary p-3">no nodes match</div>
      {/each}
    </div>
  </div>

  <div class="col-lg-7">
    {#if creating}
      <CreateArtifact
        oncreated={(id) => {
          creating = false
          selectedId = id
          load()
          onchanged?.()
        }}
        oncancel={() => (creating = false)}
      />
    {:else if selectedId}
      <NodeDetail
        id={selectedId}
        onselect={(id) => (selectedId = id)}
        onchanged={() => {
          load()
          onchanged?.()
        }}
      />
    {:else}
      <div class="text-secondary p-5 text-center border rounded bg-body-tertiary">
        select a node to inspect it — or ＋ artifact to add one
      </div>
    {/if}
  </div>
</div>
