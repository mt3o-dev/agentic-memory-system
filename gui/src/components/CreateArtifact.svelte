<script>
  import { get, post } from '../api.js'

  let { oncreated, oncancel } = $props()

  let goals = $state([])
  let goalId = $state('')
  let content = $state('')
  let type = $state('decision')
  let tier = $state('short-term')
  let facets = $state('')
  let warnings = $state([])
  let error = $state('')

  $effect(() => {
    get('/api/goals').then((g) => {
      goals = g
      if (!goalId && g.length) goalId = g[0].id
    })
  })

  async function submit() {
    error = ''
    warnings = []
    try {
      const result = await post('/api/nodes', {
        content,
        type,
        goal_ref: goalId,
        tier,
        facets: facets.split(',').map((f) => f.trim()).filter(Boolean),
      })
      warnings = result.facet_warnings ?? []
      oncreated?.(result.node_id)
    } catch (e) {
      error = e.message
    }
  }
</script>

<div class="card">
  <div class="card-header py-2 fw-semibold">new artifact</div>
  <div class="card-body d-flex flex-column gap-2">
    <select class="form-select form-select-sm" bind:value={goalId}>
      {#each goals as g}
        <option value={g.id}>goal: {g.preview}</option>
      {:else}
        <option value="">no goals — an agent (or you) must open a change first</option>
      {/each}
    </select>
    <textarea
      class="form-control form-control-sm"
      rows="4"
      placeholder="content — the artifact text"
      bind:value={content}
    ></textarea>
    <div class="d-flex gap-2">
      <select class="form-select form-select-sm w-auto" bind:value={type}>
        {#each ['decision', 'concept', 'constraint', 'issue', 'invariant'] as t}
          <option value={t}>{t}</option>
        {/each}
      </select>
      <select class="form-select form-select-sm w-auto" bind:value={tier}>
        <option value="short-term">short-term</option>
        <option value="mid-term">mid-term</option>
      </select>
      <input
        class="form-control form-control-sm flex-grow-1"
        placeholder="facets, comma-separated (optional)"
        bind:value={facets}
      />
    </div>
    {#if error}<div class="alert alert-danger py-1 mb-0">{error}</div>{/if}
    {#each warnings as w}
      <div class="alert alert-warning py-1 mb-0">{w}</div>
    {/each}
    <div class="d-flex gap-2">
      <button
        class="btn btn-sm btn-primary"
        onclick={submit}
        disabled={!goalId || !content.trim()}
      >
        create
      </button>
      <button class="btn btn-sm btn-outline-secondary" onclick={() => oncancel?.()}>
        cancel
      </button>
    </div>
  </div>
</div>
