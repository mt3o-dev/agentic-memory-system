<script>
  import { get, post } from '../api.js'

  let { onchanged } = $props()

  let changes = $state([])
  let sweepResult = $state(null)
  let error = $state('')

  async function load() {
    try {
      changes = await get('/api/changes')
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  $effect(() => {
    load()
  })

  async function toggle(change) {
    try {
      await post(`/api/changes/${change.id}/${change.active ? 'deactivate' : 'activate'}`)
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    }
  }

  async function sweep() {
    try {
      sweepResult = await post('/api/sweep')
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    }
  }
</script>

<p class="text-secondary small">
  Each change is a liveness root: content scoped to an <strong>active</strong> change
  stays live; deactivate (merge) and <em>sweep</em> to archive its short/mid-term detail.
  Foundations (long-term, lifetime) survive sweeps regardless.
</p>

{#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

<div class="d-flex mb-2">
  <button class="btn btn-sm btn-outline-primary" onclick={sweep}>run sweep (mark &amp; archive)</button>
  {#if sweepResult}
    <span class="small text-secondary ms-2 align-self-center">
      {Object.keys(sweepResult.changed).length} node(s) changed state
      {#if Object.keys(sweepResult.changed).length > 0}
        — {Object.entries(sweepResult.changed)
          .map(([id, archived]) => `${id.slice(0, 8)}→${archived ? 'archived' : 'live'}`)
          .join(', ')}
      {/if}
    </span>
  {/if}
</div>

<table class="table table-hover align-middle">
  <thead>
    <tr class="small text-secondary">
      <th>change</th>
      <th>description</th>
      <th>scoped nodes</th>
      <th>state</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {#each changes as change (change.id)}
      <tr>
        <td><code>{change.path}</code></td>
        <td class="small text-secondary">{change.preview}</td>
        <td>{change.scoped_nodes}</td>
        <td>
          <span class="badge {change.active ? 'text-bg-success' : 'text-bg-secondary'}">
            {change.active ? 'active' : 'dormant'}
          </span>
        </td>
        <td class="text-end">
          <button class="btn btn-sm btn-outline-secondary" onclick={() => toggle(change)}>
            {change.active ? 'deactivate' : 'activate'}
          </button>
        </td>
      </tr>
    {:else}
      <tr><td colspan="5" class="text-secondary text-center py-4">no changes yet — an agent opens one via create_change</td></tr>
    {/each}
  </tbody>
</table>
