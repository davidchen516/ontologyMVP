// ontologyMVP Neo4j schema baseline
// Target: Neo4j 5.x
// Neo4j is a query projection. PostgreSQL remains the source of truth.

// ---------------------------------------------------------------------------
// Uniqueness constraints
// ---------------------------------------------------------------------------

CREATE CONSTRAINT company_id_unique IF NOT EXISTS
FOR (n:Company) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT security_id_unique IF NOT EXISTS
FOR (n:Security) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT exchange_id_unique IF NOT EXISTS
FOR (n:Exchange) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT industry_id_unique IF NOT EXISTS
FOR (n:Industry) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT concept_id_unique IF NOT EXISTS
FOR (n:Concept) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT theme_id_unique IF NOT EXISTS
FOR (n:Theme) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT product_id_unique IF NOT EXISTS
FOR (n:Product) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT organization_id_unique IF NOT EXISTS
FOR (n:Organization) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (n:Person) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT claim_id_unique IF NOT EXISTS
FOR (n:Claim) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS
FOR (n:Evidence) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (n:Document) REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Lookup indexes
// ---------------------------------------------------------------------------

CREATE INDEX company_name_idx IF NOT EXISTS
FOR (n:Company) ON (n.canonical_name);

CREATE INDEX company_credit_code_idx IF NOT EXISTS
FOR (n:Company) ON (n.unified_social_credit_code);

CREATE INDEX security_ts_code_idx IF NOT EXISTS
FOR (n:Security) ON (n.ts_code);

CREATE INDEX security_symbol_idx IF NOT EXISTS
FOR (n:Security) ON (n.symbol);

CREATE INDEX industry_taxonomy_code_idx IF NOT EXISTS
FOR (n:Industry) ON (n.taxonomy, n.external_code);

CREATE INDEX concept_platform_code_idx IF NOT EXISTS
FOR (n:Concept) ON (n.source_platform, n.external_code);

CREATE INDEX product_name_idx IF NOT EXISTS
FOR (n:Product) ON (n.canonical_name);

CREATE INDEX product_iri_idx IF NOT EXISTS
FOR (n:Product) ON (n.iri);

CREATE INDEX theme_name_idx IF NOT EXISTS
FOR (n:Theme) ON (n.canonical_name);

CREATE INDEX claim_status_idx IF NOT EXISTS
FOR (n:Claim) ON (n.claim_status);

CREATE INDEX claim_predicate_idx IF NOT EXISTS
FOR (n:Claim) ON (n.predicate_code);

CREATE INDEX claim_temporal_idx IF NOT EXISTS
FOR (n:Claim) ON (n.valid_from, n.valid_to);

CREATE INDEX document_published_at_idx IF NOT EXISTS
FOR (n:Document) ON (n.published_at);

// Optional full-text indexes. Enable after language/tokenizer validation.
CREATE FULLTEXT INDEX company_product_search IF NOT EXISTS
FOR (n:Company|Product|Theme|Concept)
ON EACH [n.canonical_name, n.name, n.aliases_text];

// ---------------------------------------------------------------------------
// Canonical projection patterns
// ---------------------------------------------------------------------------

// Company node
// MERGE (c:Company {id: $id})
// SET c.canonical_name = $canonical_name,
//     c.legal_name = $legal_name,
//     c.unified_social_credit_code = $unified_social_credit_code,
//     c.status = $status,
//     c.updated_at = $updated_at;

// Security issuance
// MATCH (c:Company {id: $company_id})
// MATCH (s:Security {id: $security_id})
// MERGE (c)-[r:ISSUES {relation_key: $relation_key}]->(s)
// SET r.valid_from = $valid_from,
//     r.valid_to = $valid_to,
//     r.recorded_at = $recorded_at,
//     r.source_record_id = $source_record_id;

// Platform concept membership. This is a classification, not an operating fact.
// MATCH (c:Company {id: $company_id})
// MATCH (x:Concept {id: $concept_id})
// MERGE (c)-[r:TAGGED_AS {membership_key: $membership_key}]->(x)
// SET r.source_platform = $source_platform,
//     r.snapshot_date = $snapshot_date,
//     r.is_member = $is_member,
//     r.source_record_id = $source_record_id;

// Claim-centered operating fact
// MATCH (c:Company {id: $company_id})
// MATCH (p:Product {id: $product_id})
// MERGE (cl:Claim {id: $claim_id})
// SET cl.predicate_code = $predicate_code,
//     cl.claim_status = $claim_status,
//     cl.business_stage = $business_stage,
//     cl.evidence_state = $evidence_state,
//     cl.valid_from = $valid_from,
//     cl.valid_to = $valid_to,
//     cl.recorded_at = $recorded_at,
//     cl.superseded_at = $superseded_at,
//     cl.confidence = $confidence,
//     cl.ontology_version = $ontology_version
// MERGE (c)-[:HAS_CLAIM]->(cl)
// MERGE (cl)-[:OBJECT]->(p);

// Materialized edge for fast traversal. Every operating edge MUST carry claim_id.
// MATCH (c:Company {id: $company_id})
// MATCH (p:Product {id: $product_id})
// MERGE (c)-[r:PRODUCES {claim_id: $claim_id}]->(p)
// SET r.claim_status = $claim_status,
//     r.business_stage = $business_stage,
//     r.evidence_state = $evidence_state,
//     r.valid_from = $valid_from,
//     r.valid_to = $valid_to,
//     r.recorded_at = $recorded_at,
//     r.superseded_at = $superseded_at,
//     r.confidence = $confidence,
//     r.ontology_version = $ontology_version;

// Evidence path
// MATCH (cl:Claim {id: $claim_id})
// MATCH (e:Evidence {id: $evidence_id})
// MERGE (cl)-[r:SUPPORTED_BY {support_type: $support_type}]->(e)
// SET r.source_weight = $source_weight;

// Product taxonomy and theme
// MATCH (p:Product {id: $product_id})
// MATCH (parent:Product {id: $parent_product_id})
// MERGE (p)-[:SUBCLASS_OF]->(parent);
//
// MATCH (p:Product {id: $product_id})
// MATCH (t:Theme {id: $theme_id})
// MERGE (p)-[:PART_OF]->(t);

// ---------------------------------------------------------------------------
// Query templates
// ---------------------------------------------------------------------------

// 1. Verified products for a company at a point in time
// MATCH (c:Company {id: $company_id})-[r:PRODUCES]->(p:Product)
// MATCH (cl:Claim {id: r.claim_id})-[:SUPPORTED_BY]->(e:Evidence)
// WHERE cl.claim_status = 'ACCEPTED'
//   AND (cl.valid_from IS NULL OR cl.valid_from <= $as_of)
//   AND (cl.valid_to IS NULL OR cl.valid_to > $as_of)
// RETURN c, p, cl, collect(e) AS evidence;

// 2. Companies in a theme with verified operating evidence
// MATCH (c:Company)-[r:PRODUCES]->(p:Product)
// MATCH (p)-[:SUBCLASS_OF*0..4]->(ancestor:Product)
// MATCH (ancestor)-[:PART_OF]->(t:Theme {id: $theme_id})
// MATCH (cl:Claim {id: r.claim_id})
// WHERE cl.claim_status = 'ACCEPTED'
//   AND cl.business_stage IN $business_stages
//   AND (cl.valid_from IS NULL OR cl.valid_from <= $as_of)
//   AND (cl.valid_to IS NULL OR cl.valid_to > $as_of)
// RETURN DISTINCT c.id AS company_id,
//                 p.id AS product_id,
//                 cl.id AS claim_id;

// 3. Companies with concept classification but no accepted operating claim
// MATCH (c:Company)-[:TAGGED_AS]->(x:Concept)
// WHERE x.id IN $concept_ids
//   AND NOT EXISTS {
//       MATCH (c)-[:HAS_CLAIM]->(cl:Claim)
//       WHERE cl.claim_status = 'ACCEPTED'
//         AND cl.predicate_code IN ['PRODUCES', 'DEVELOPS', 'HAS_REVENUE_FROM']
//   }
// RETURN DISTINCT c, collect(DISTINCT x) AS concepts;

// 4. Explanation path from company to theme
// MATCH path = (c:Company {id: $company_id})
//              -[:PRODUCES]->(:Product)
//              -[:SUBCLASS_OF*0..4]->(:Product)
//              -[:PART_OF]->(t:Theme {id: $theme_id})
// RETURN path
// ORDER BY length(path)
// LIMIT 10;

// ---------------------------------------------------------------------------
// Projection and reconciliation invariants
// ---------------------------------------------------------------------------

// A. Neo4j is rebuildable from PostgreSQL master data + ACCEPTED claims.
// B. Platform TAGGED_AS relationships are never converted to PRODUCES.
// C. Every PRODUCES/DEVELOPS/SUPPLIES_TO/HAS_REVENUE_FROM relationship has claim_id.
// D. Claim status changes are propagated; SUPERSEDED/REJECTED edges are removed or
//    marked inactive according to projector policy.
// E. Nodes use application UUIDs or stable IRIs, never Neo4j internal IDs.
// F. Graph writes are idempotent through relation_key/claim_id/idempotency_key.
// G. Graph Projector performs read-after-write checks after each batch.

// Reconciliation examples
// Count accepted Claim nodes
// MATCH (cl:Claim {claim_status: 'ACCEPTED'}) RETURN count(cl);

// Find operating edges without claim_id (must return zero)
// MATCH ()-[r]->()
// WHERE type(r) IN ['PRODUCES', 'DEVELOPS', 'SUPPLIES_TO', 'HAS_REVENUE_FROM']
//   AND r.claim_id IS NULL
// RETURN type(r), count(r);

// Find dangling materialized edges without Claim node (must return zero)
// MATCH ()-[r]->()
// WHERE r.claim_id IS NOT NULL
//   AND NOT EXISTS { MATCH (:Claim {id: r.claim_id}) }
// RETURN type(r), r.claim_id;
