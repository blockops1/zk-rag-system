//! Kurier/zkVerify API client.
//!
//! API base: `https://api.kurier.xyz/api/v1`
//!
//! ## Kurier API flow
//!
//! 1. `POST /register-vk/{api_key}` — register circuit VK (one-time per circuit design)
//!    - Body: `RegisterVkRequest`
//!    - Response: `RegisterVkResponse { vk_hash: String }`
//!
//! 2. `POST /submit-proof/{api_key}` — submit proof for verification
//!    - Body: `SubmitProofRequest`
//!    - Response: `SubmitProofResponse { job_id, optimistic_verify, error }`
//!
//! 3. `GET /job-status/{job_id}/{api_key}` — poll until COMPLETED or FAILED
//!    - Response: `JobStatusResponse`

use serde::{Deserialize, Serialize};
use std::time::Duration;

const API_BASE: &str = "https://api.kurier.xyz/api/v1";

// ─────────────────────────────────────────────────────────────────────────────
// Error types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(thiserror::Error, Debug)]
pub enum KurierError {
    #[error("HTTP transport error")]
    Http(String),

    #[error("Kurier API error {code}: {message}")]
    Api { code: u16, message: String },

    #[error("response body is not valid JSON: {0}")]
    Parse(#[from] serde_json::Error),

    #[error("job failed on zkVerify: {0}")]
    JobFailed(String),
}

impl From<ureq::Error> for KurierError {
    fn from(e: ureq::Error) -> Self {
        match e {
            ureq::Error::Status(code, resp) => {
                let message = resp.into_string().unwrap_or_default();
                KurierError::Api { code, message }
            }
            ureq::Error::Transport(t) => KurierError::Http(t.to_string()),
        }
    }
}

impl From<std::io::Error> for KurierError {
    fn from(e: std::io::Error) -> Self {
        KurierError::Http(e.to_string())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Request / Response types
// ─────────────────────────────────────────────────────────────────────────────

/// POST /register-vk/{api_key}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RegisterVkRequest<'a> {
    pub proof_type: &'a str,
    pub proof_options: ProofOptions<'a>,
    pub vk: &'a serde_json::Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProofOptions<'a> {
    pub hash_function: &'a str,
}

impl ProofOptions<'_> {
    pub fn poseidon() -> Self {
        Self {
            hash_function: "poseidon",
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct RegisterVkResponse {
    #[serde(alias = "vkHash")]
    pub vk_hash: String,
}

/// POST /submit-proof/{api_key}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitProofRequest<'a> {
    pub proof_data: ProofData<'a>,
    pub proof_type: &'a str,
    pub proof_options: ProofOptions<'a>,
    #[serde(default)]
    pub chain_id: Option<u32>,
    pub vk_registered: bool,
    #[serde(default)]
    pub submission_mode: &'a str,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProofData<'a> {
    /// Hex-encoded proof bytes (0x-prefixed).
    pub proof: &'a str,
    /// Hex-encoded public signals (0x-prefixed).
    pub public_signals: &'a str,
    /// Hex-encoded verification key (0x-prefixed). Only needed when vk_registered=false.
    pub vk: Option<&'a str>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitProofResponse {
    pub job_id: String,
    pub optimistic_verify: Option<String>,
    pub error: Option<String>,
}

/// GET /job-status/{job_id}/{api_key}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobStatusResponse {
    pub id: String,
    pub status: String,
    pub zkverify_status: Option<String>,
    pub error_message: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Client
// ─────────────────────────────────────────────────────────────────────────────

pub struct KurierClient {
    api_key: String,
    chain_id: u32,
}

impl KurierClient {
    pub fn new(api_key: impl Into<String>, chain_id: u32) -> Self {
        Self {
            api_key: api_key.into(),
            chain_id,
        }
    }

    /// Register a verification key and return the vk_hash.
    ///
    /// The vk_hash is circuit-specific — only register once per circuit design,
    /// then cache and reuse for all subsequent proof submissions.
    pub fn register_vk(&self, vk_json: &serde_json::Value) -> Result<String, KurierError> {
        let url = format!("{}/register-vk/{}", API_BASE, self.api_key);

        let req = RegisterVkRequest {
            proof_type: "plonky2",
            proof_options: ProofOptions::poseidon(),
            vk: vk_json,
        };

        let resp: RegisterVkResponse = ureq::post(&url)
            .set("Content-Type", "application/json")
            .send_json(req)?
            .into_json()?;

        Ok(resp.vk_hash)
    }

    /// Submit a proof for verification.
    ///
    /// - `vk_hash`: Pre-registered VK hash from `register_vk()`.
    /// - `proof_hex`: 0x-prefixed hex of raw proof bytes.
    /// - `public_signals_hex`: 0x-prefixed hex of public signals.
    pub fn submit_proof(
        &self,
        _vk_hash: &str,
        proof_hex: &str,
        public_signals_hex: &str,
    ) -> Result<SubmitProofResponse, KurierError> {
        let url = format!("{}/submit-proof/{}", API_BASE, self.api_key);

        let req = SubmitProofRequest {
            proof_data: ProofData {
                proof: proof_hex,
                public_signals: public_signals_hex,
                vk: None,
            },
            proof_type: "plonky2",
            proof_options: ProofOptions::poseidon(),
            chain_id: Some(self.chain_id),
            vk_registered: true,
            submission_mode: "attestation",
        };

        let resp: SubmitProofResponse = ureq::post(&url)
            .set("Content-Type", "application/json")
            .send_json(req)?
            .into_json()?;

        Ok(resp)
    }

    /// Poll job status until terminal state (COMPLETED/FAILED).
    ///
    /// Returns the final `JobStatusResponse`.
    /// Polls every `interval_secs`, fails after `max_wait_secs` total.
    pub fn wait_for_job(
        &self,
        job_id: &str,
        interval_secs: u64,
        max_wait_secs: u64,
    ) -> Result<JobStatusResponse, KurierError> {
        let start = std::time::Instant::now();
        let interval = Duration::from_secs(interval_secs);

        loop {
            let status = self.get_job_status(job_id)?;
            let state = status.status.to_lowercase();

            tracing::info!(
                job_id = %job_id,
                status = %status.status,
                zkverify_status = ?status.zkverify_status,
                "Kurier job status"
            );

            match state.as_str() {
                "completed" | "successful" | "done" | "verified" => {
                    return Ok(status);
                }
                "failed" | "rejected" | "invalid" => {
                    let msg = status
                        .error_message
                        .or(status.zkverify_status)
                        .unwrap_or_else(|| "unknown error".to_string());
                    return Err(KurierError::JobFailed(msg));
                }
                _ => {
                    if start.elapsed().as_secs() > max_wait_secs {
                        return Err(KurierError::JobFailed(format!(
                            "timeout after {}s waiting for job {}",
                            max_wait_secs, job_id
                        )));
                    }
                    std::thread::sleep(interval);
                }
            }
        }
    }

    /// Get current job status (single poll, no waiting).
    pub fn get_job_status(&self, job_id: &str) -> Result<JobStatusResponse, KurierError> {
        let url = format!("{}/job-status/{}/{}", API_BASE, job_id, self.api_key);
        let resp: JobStatusResponse = ureq::get(&url)
            .set("Accept", "application/json")
            .call()?
            .into_json()?;
        Ok(resp)
    }
}
