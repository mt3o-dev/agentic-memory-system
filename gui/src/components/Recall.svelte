<script>
  import { get, tierBadge } from '../api.js'

  let { openNode } = $props()

  let goals = $state([])
  let goalId = $state('')
  let query = $state('')
  let results = $state(null)
  let error = $state('')

  $effect(() => {
    get('/api/goals').then((g) => {
      goals = g
      if (!goalId && g.length) goalId = g[0].id
    })
  })

  async function run() {
    try {
      results = await get(
        `/api/recall?goal=${encodeURIComponent(goalId)}&query=${encodeURIComponent(query)}`
      )
      error = ''
    } catch (e) {
      error = e.message
      results = null
    }
  }

  let maxScore = $derived(results?.length ? Math.max(...results.map((r) => r.score)) : 1)
</script>

<p class="text-secondary small">
  Preview exactly what an agent's <code>recall_context</code> selects — goal-dominant
  multi-seed PageRank — but <em>with</em> the scores the agent never sees.
  Deterministic: same graph + same query → same ranking.
</p>

{#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

<div class="d-flex gap-2 mb-3 flex-wrap">
  <select class="form-select form-select-sm w-auto" bind:value={goalId}>
    {#each goals as g}
      <option value={g.id}>{g.preview}</option>
    {:else}
      <option value="">no goals in the graph yet</option>
    {/each}
  </select>
  <input
    class="form-control form-control-sm w-auto flex-grow-1"
    placeholder="query — what is the agent about to work on?"
    bind:value={query}
    onkeydown={(e) => e.key === 'Enter' && run()}
  />
  <button class="btn btn-sm btn-primary" onclick={run} disabled={!goalId}>recall</button>
</div>

{#if results}
  <table class="table align-middle">
    <thead>
      <tr class="small text-secondary">
        <th style="width: 3rem">#</th>
        <th>node</th>
        <th>content</th>
        <th style="width: 18rem">score</th>
      </tr>
    </thead>
    <tbody>
      {#each results as r, i (r.id)}
        <tr>
          <td class="text-secondary">{i + 1}</td>
          <td>
            <button class="btn btn-link btn-sm p-0" onclick={() => openNode(r.id)}>
              <code>{r.path}</code>
            </button>
            <div>
              <span class="badge text-bg-light border">{r.type}</span>
              <span class="badge {tierBadge[r.tier]}">{r.tier}</span>
              {#if r.needs_review}<span class="badge text-bg-danger">disputed</span>{/if}
            </div>
          </td>
          <td class="small text-secondary">{r.preview}</td>
          <td>
            <div class="d-flex align-items-center gap-2">
              <div class="progress flex-grow-1" style="height: 6px">
                <div class="progress-bar" style="width: {(r.score / maxScore) * 100}%"></div>
              </div>
              <code class="small">{r.score.toFixed(3)}</code>
            </div>
          </td>
        </tr>
      {:else}
        <tr><td colspan="4" class="text-secondary text-center py-4">nothing reachable from this goal</td></tr>
      {/each}
    </tbody>
  </table>
{/if}
