<script>
  import { get } from './api.js'
  import Browse from './components/Browse.svelte'
  import ReviewQueue from './components/ReviewQueue.svelte'
  import Changes from './components/Changes.svelte'
  import Recall from './components/Recall.svelte'

  let tab = $state('browse')
  let selectedId = $state(null)
  let health = $state(null)

  async function refreshHealth() {
    try {
      health = await get('/api/health')
    } catch {
      health = null
    }
  }

  function openNode(id) {
    selectedId = id
    tab = 'browse'
  }

  $effect(() => {
    tab
    refreshHealth()
  })

  const tabs = [
    ['browse', 'Browse'],
    ['review', 'Review'],
    ['changes', 'Changes'],
    ['recall', 'Recall'],
  ]
</script>

<nav class="navbar navbar-expand border-bottom bg-body-tertiary px-3">
  <span class="navbar-brand fw-semibold">agentic memory</span>
  <ul class="nav nav-pills me-auto">
    {#each tabs as [key, label]}
      <li class="nav-item">
        <button
          class="nav-link py-1 {tab === key ? 'active' : ''}"
          onclick={() => (tab = key)}
        >
          {label}
          {#if key === 'review' && health?.flagged}
            <span class="badge text-bg-danger ms-1">{health.flagged}</span>
          {/if}
        </button>
      </li>
    {/each}
  </ul>
  {#if health}
    <small class="text-secondary">
      {health.nodes} nodes · {health.events} events · {health.active_changes} active
      {health.active_changes === 1 ? 'change' : 'changes'} · {health.archived} archived
      {#if health.edgeless > 0}
        · <span class="text-warning-emphasis">{health.edgeless} edgeless</span>
      {/if}
    </small>
  {/if}
</nav>

<main class="container-fluid py-3">
  {#if tab === 'browse'}
    <Browse bind:selectedId onchanged={refreshHealth} />
  {:else if tab === 'review'}
    <ReviewQueue {openNode} onchanged={refreshHealth} />
  {:else if tab === 'changes'}
    <Changes onchanged={refreshHealth} />
  {:else if tab === 'recall'}
    <Recall {openNode} />
  {/if}
</main>

<style>
  main {
    max-width: 1400px;
  }
</style>
