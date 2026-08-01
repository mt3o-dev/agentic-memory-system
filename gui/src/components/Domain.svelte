<script>
  import { get, post } from '../api.js'

  let { openNode, onchanged } = $props()

  let entities = $state([])
  let candidates = $state([])
  let showRetired = $state(false)
  let error = $state('')
  // The wording of a consolidated node is the human's, not the agent's — the agent
  // brought the cluster and a suggestion, this is where a person commits the sentence.
  let drafting = $state(null)
  let draft = $state({ content: '', type: 'concept', tier: 'mid-term', confirmed: false })

  async function load() {
    try {
      entities = await get(`/api/entities${showRetired ? '?retired=1' : ''}`)
      candidates = await get('/api/consolidation/candidates')
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  $effect(() => {
    showRetired
    load()
  })

  async function rule(id, action) {
    try {
      await post(`/api/entities/${id}/${action}`, {})
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    }
  }

  function startDraft(candidate) {
    drafting = candidate
    draft = {
      content: '',
      type: candidate.suggested_type,
      tier: 'mid-term',
      confirmed: false,
    }
  }

  async function commit() {
    try {
      await post('/api/consolidate', {
        instance_ids: drafting.node_ids,
        content: draft.content,
        type: draft.type,
        tier: draft.tier,
        tier_confirmed: draft.confirmed,
        reason: `consolidated ${drafting.node_ids.length} instances of ${drafting.facet}`,
      })
      drafting = null
      await load()
      onchanged?.()
    } catch (e) {
      error = e.message
    }
  }

  const statusBadge = {
    proposed: 'text-bg-warning',
    confirmed: 'text-bg-success',
    retired: 'text-bg-secondary',
  }
</script>

{#if error}<div class="alert alert-danger py-1">{error}</div>{/if}

<h6 class="mt-1">Domain entities</h6>
<p class="text-secondary small">
  The project's ubiquitous language. Entities are the 4th dynamics class: they don't
  decay with age, they aren't consolidated, and they survive every sweep regardless of
  tier — the domain outlives the changes that name it. Agents may only ever
  <strong>propose</strong>; ratifying an entity is yours. Retire (don't archive) one the
  domain no longer has: its <code>ABOUT</code> edges stay traceable, it just leaves the
  root set.
</p>

<div class="form-check form-switch small mb-2">
  <input class="form-check-input" type="checkbox" id="show-retired" bind:checked={showRetired} />
  <label class="form-check-label text-secondary" for="show-retired">show retired</label>
</div>

<table class="table table-hover align-middle">
  <thead>
    <tr class="small text-secondary">
      <th>entity</th>
      <th>definition</th>
      <th>attached</th>
      <th>provenance</th>
      <th>status</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {#each entities as entity (entity.id)}
      <tr>
        <td>
          <button class="btn btn-link btn-sm p-0" onclick={() => openNode(entity.id)}>
            <code>{entity.path}</code>
          </button>
        </td>
        <td class="small text-secondary" style="max-width: 28rem">{entity.preview}</td>
        <td>{entity.attached}</td>
        <td class="small text-secondary" style="max-width: 18rem">
          {entity.provenance ?? '—'}
        </td>
        <td><span class="badge {statusBadge[entity.status]}">{entity.status}</span></td>
        <td class="text-end text-nowrap">
          {#if entity.status !== 'confirmed'}
            <button class="btn btn-sm btn-success me-1" onclick={() => rule(entity.id, 'confirm')}>
              confirm
            </button>
          {/if}
          {#if entity.status !== 'retired'}
            <button
              class="btn btn-sm btn-outline-secondary"
              onclick={() => rule(entity.id, 'retire')}
            >
              retire
            </button>
          {/if}
        </td>
      </tr>
    {:else}
      <tr>
        <td colspan="6" class="text-secondary text-center py-4">
          no domain entities — an agent proposes them via capture_entity
        </td>
      </tr>
    {/each}
  </tbody>
</table>

<h6 class="mt-4">Consolidation candidates</h6>
<p class="text-secondary small">
  Recurrence worth abstracting: artifacts saying variations of one thing across several
  changes. The detector is deterministic and read-only; minting the abstraction is yours,
  because a consolidated node exists to be <em>promoted past the sweep</em>. Consolidation
  only ever adds — the instances are never edited, archived, or re-tiered.
</p>

{#each candidates as candidate (candidate.node_ids[0])}
  <div class="card mb-2">
    <div class="card-body py-2">
      <div class="d-flex align-items-center">
        <span class="badge text-bg-info me-2">{candidate.facet}</span>
        <small class="text-secondary">
          {candidate.node_ids.length} instances across {candidate.scopes.length} changes ·
          suggested type <code>{candidate.suggested_type}</code>
        </small>
        <button class="btn btn-sm btn-primary ms-auto" onclick={() => startDraft(candidate)}>
          consolidate…
        </button>
      </div>
      <ul class="small text-secondary mt-2 mb-0">
        {#each candidate.instances as instance (instance.id)}
          <li>
            <button class="btn btn-link btn-sm p-0" onclick={() => openNode(instance.id)}>
              <code>{instance.path}</code>
            </button>
            — {instance.preview}
          </li>
        {/each}
      </ul>
    </div>
  </div>
{:else}
  <p class="text-secondary small fst-italic">
    no candidates — no cross-change recurrence detected
  </p>
{/each}

{#if drafting}
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.4)">
    <div class="modal-dialog modal-lg">
      <div class="modal-content">
        <div class="modal-header py-2">
          <h6 class="modal-title">
            Consolidate {drafting.node_ids.length} instances of
            <code>{drafting.facet}</code>
          </h6>
          <button
            class="btn-close"
            aria-label="close"
            onclick={() => (drafting = null)}
          ></button>
        </div>
        <div class="modal-body">
          <label class="form-label small" for="consolidated-body">
            The abstraction, in your words — one statement, readable cold
          </label>
          <textarea
            id="consolidated-body"
            class="form-control mb-2"
            rows="3"
            bind:value={draft.content}
          ></textarea>
          <div class="row g-2">
            <div class="col">
              <label class="form-label small" for="consolidated-type">type</label>
              <select id="consolidated-type" class="form-select form-select-sm" bind:value={draft.type}>
                {#each ['concept', 'constraint', 'invariant', 'decision', 'issue'] as t}
                  <option value={t}>{t}</option>
                {/each}
              </select>
            </div>
            <div class="col">
              <label class="form-label small" for="consolidated-tier">tier</label>
              <select id="consolidated-tier" class="form-select form-select-sm" bind:value={draft.tier}>
                {#each ['short-term', 'mid-term', 'long-term', 'lifetime'] as t}
                  <option value={t}>{t}</option>
                {/each}
              </select>
            </div>
          </div>
          {#if draft.tier === 'lifetime'}
            <div class="form-check mt-2">
              <input
                class="form-check-input"
                type="checkbox"
                id="lifetime-confirm"
                bind:checked={draft.confirmed}
              />
              <label class="form-check-label small" for="lifetime-confirm">
                I confirm this belongs in the lifetime root set, permanently
              </label>
            </div>
          {/if}
        </div>
        <div class="modal-footer py-2">
          <button class="btn btn-sm btn-outline-secondary" onclick={() => (drafting = null)}>
            cancel
          </button>
          <button
            class="btn btn-sm btn-primary"
            disabled={!draft.content.trim()}
            onclick={commit}
          >
            consolidate
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}
