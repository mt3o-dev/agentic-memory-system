<script>
  import { get, post } from '../api.js'

  let { openNode, onchanged } = $props()

  let queue = $state([])
  let error = $state('')

  async function load() {
    try {
      queue = await get('/api/review')
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  $effect(() => {
    load()
  })

  async function clearFlag(id) {
    try {
      await post(`/api/nodes/${id}/clear-flag`, { reason: 'reviewed in GUI: false alarm' })
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    }
  }
</script>

<p class="text-secondary small">
  Nodes flagged by a contradiction and waiting for a human (or evaluator) verdict.
  Clearing restores the node's score for free — the demotion was flag-driven, trust was
  never decremented. The <em>rules verdict</em> column is the deterministic first tier
  of the resolution ladder, as a hint.
</p>

{#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

<table class="table table-hover align-middle">
  <thead>
    <tr class="small text-secondary">
      <th>node</th>
      <th>content</th>
      <th>severity</th>
      <th>rules verdict</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {#each queue as item (item.id)}
      <tr>
        <td>
          <button class="btn btn-link btn-sm p-0" onclick={() => openNode(item.id)}>
            <code>{item.path}</code>
          </button>
        </td>
        <td class="small text-secondary" style="max-width: 30rem">{item.preview}</td>
        <td><span class="badge text-bg-danger">{item.severity.toFixed(1)}</span></td>
        <td>
          <span
            class="badge {item.resolver_verdict === 'auto_clear'
              ? 'text-bg-success'
              : 'text-bg-secondary'}"
          >
            {item.resolver_verdict}
          </span>
        </td>
        <td class="text-end">
          <button class="btn btn-sm btn-outline-success" onclick={() => clearFlag(item.id)}>
            clear (false alarm)
          </button>
        </td>
      </tr>
    {:else}
      <tr><td colspan="5" class="text-secondary text-center py-4">review queue is empty 🎉</td></tr>
    {/each}
  </tbody>
</table>
