# Offline configuration tests. The mocked provider creates no Google resources.
mock_provider "google" {
  mock_data "google_secret_manager_secret_version_access" {
    defaults = {
      secret_data = "{\"username\":\"postgres\",\"password\":\"fictional-test-only-password\"}"
    }
  }
}

variables {
  project_id          = "fictional-project"
  registry_repository = "fictional-images"
  image               = "us-central1-docker.pkg.dev/fictional-project/fictional-images/workbench@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "initialize_before_starting_services" {
  command = plan

  assert {
    condition     = length(google_cloud_run_v2_service.api) == 0 && length(google_cloud_run_v2_worker_pool.worker) == 0 && length(google_cloud_run_v2_service.web) == 0
    error_message = "The initial phase must not start API, worker or browser services."
  }

  assert {
    condition     = google_cloud_run_v2_job.bootstrap.template[0].template[0].max_retries == 0 && google_cloud_run_v2_job.operator.template[0].template[0].max_retries == 0
    error_message = "Initialization must use explicit finite jobs without automatic retries."
  }

  assert {
    condition     = google_sql_database_instance.workspace.settings[0].connector_enforcement == "REQUIRED" && google_sql_database_instance.workspace.deletion_protection
    error_message = "Database connections must require the Cloud SQL connector and deletion protection."
  }
}

run "start_reviewed_services" {
  command = plan

  variables {
    deploy_services = true
    worker_replicas = 2
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api) == 1 && length(google_cloud_run_v2_worker_pool.worker) == 1 && length(google_cloud_run_v2_service.web) == 1
    error_message = "The enabled phase must start all three application roles."
  }

  assert {
    condition     = google_cloud_run_v2_worker_pool.worker[0].scaling[0].manual_instance_count == 2
    error_message = "The background worker must retain the approved positive replica count."
  }

  assert {
    condition     = length(google_cloud_run_v2_service.web[0].template[0].volumes) == 0 && length(google_cloud_run_v2_service.api[0].template[0].volumes) == 1
    error_message = "Only backend services may mount the Cloud SQL connection."
  }

  assert {
    condition     = google_storage_bucket.artifacts.versioning[0].enabled && google_storage_bucket.backups.versioning[0].enabled
    error_message = "Live and recovery artifacts must retain object generations."
  }
}
