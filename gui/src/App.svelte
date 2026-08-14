<script>
  import { get } from './api.js'
  import Browse from './components/Browse.svelte'
  import ReviewQueue from './components/ReviewQueue.svelte'
  import Changes from './components/Changes.svelte'
  import Domain from './components/Domain.svelte'
  import Recall from './components/Recall.svelte'

  let tab = $state('browse')
  let selectedId = $state(null)
  let health = $state(null)
  let info = $state(null)
  let infoOpen = $state(false)

  async function refreshHealth() {
    try {
      health = await get('/api/health')
    } catch {
      health = null
    }
  }

  async function toggleInfo() {
    infoOpen = !infoOpen
    if (infoOpen && !info) {
      try {
        info = await get('/api/info')
      } catch {
        info = null
      }
    }
  }

  function closeInfo() {
    infoOpen = false
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
    ['domain', 'Domain'],
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
          {#if key === 'domain' && health?.entities_proposed}
            <span class="badge text-bg-warning ms-1">{health.entities_proposed}</span>
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
  <div class="position-relative ms-3">
    <button
      type="button"
      class="btn btn-sm btn-outline-secondary rounded-circle p-0 d-inline-flex align-items-center justify-content-center"
      style="width: 1.75rem; height: 1.75rem;"
      title="Server info"
      aria-label="Server info"
      onclick={toggleInfo}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="16"
        height="16"
        fill="currentColor"
        viewBox="0 0 16 16"
      >
        <path
          d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14m0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16"
        />
        <path
          d="m8.93 6.588-2.29.287-.082.38.45.083c.294.07.352.176.288.469l-.738 3.468c-.194.897.105 1.319.808 1.319.545 0 1.178-.252 1.465-.598l.088-.416c-.2.176-.492.246-.686.246-.275 0-.375-.193-.304-.533zM9 4.5a1 1 0 1 1-2 0 1 1 0 0 1 2 0"
        />
      </svg>
    </button>
    {#if infoOpen}
      <div
        class="card shadow position-absolute end-0 mt-2"
        style="width: 22rem; z-index: 1050;"
      >
        <div class="card-header d-flex justify-content-between align-items-center py-2">
          <strong class="small">Server info</strong>
          <button
            type="button"
            class="btn-close"
            aria-label="Close"
            onclick={closeInfo}
          ></button>
        </div>
        <div class="card-body py-2">
          {#if !info}
            <div class="text-secondary small">loading…</div>
          {:else}
            <dl class="row small mb-0">
              <dt class="col-4">Project</dt>
              <dd class="col-8 text-break">{info.project}</dd>
              <dt class="col-4">Version</dt>
              <dd class="col-8 text-break">{info.version ?? '—'}</dd>
              <dt class="col-4">PWD</dt>
              <dd class="col-8 text-break"><code class="small">{info.cwd}</code></dd>
              <dt class="col-4">DB path</dt>
              <dd class="col-8 text-break"><code class="small">{info.db_path ?? '—'}</code></dd>
              <dt class="col-4">Python</dt>
              <dd class="col-8 text-break">{info.python_version}</dd>
              <dt class="col-4">PID</dt>
              <dd class="col-8 text-break">{info.pid}</dd>
            </dl>
          {/if}
        </div>
      </div>
    {/if}
  </div>
</nav>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape') closeInfo()
  }}
  onclick={(e) => {
    if (infoOpen && !e.target.closest('.position-relative.ms-3')) closeInfo()
  }}
/>

<main class="container-fluid py-3">
  {#if tab === 'browse'}
    <Browse bind:selectedId onchanged={refreshHealth} />
  {:else if tab === 'review'}
    <ReviewQueue {openNode} onchanged={refreshHealth} />
  {:else if tab === 'domain'}
    <Domain {openNode} onchanged={refreshHealth} />
  {:else if tab === 'changes'}
    <Changes {openNode} onchanged={refreshHealth} />
  {:else if tab === 'recall'}
    <Recall {openNode} />
  {/if}
</main>

