import json
import boto3
import sys
from botocore.exceptions import ClientError

def create_s3_batch_job(json_file_path):
    try:
        # Load job parameters from JSON file
        with open(json_file_path, 'r') as f:
            job_config = json.load(f)

        # Extract AccountId (required for the API call)
        account_id = job_config.pop("AccountId")

        # Create S3 Control client
        s3control = boto3.client('s3control')

        # Create the job
        response = s3control.create_job(
            AccountId=account_id,
            **job_config
        )

        print("Job created successfully:")
        print(json.dumps(response, indent=2))

    except FileNotFoundError:
        print(f"Error: File {json_file_path} not found.")
    except ClientError as e:
        print(f"ClientError: {e.response['Error']['Message']}")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python create_s3_batch_job.py job_config.json")
    else:
        create_s3_batch_job(sys.argv[1])
