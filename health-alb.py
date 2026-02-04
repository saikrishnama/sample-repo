import boto3
import pandas as pd

elb = boto3.client("elbv2")
asg = boto3.client("autoscaling")
ec2 = boto3.client("ec2")

rows = []

# ---------- GET ALL LOAD BALANCERS ----------
lbs = elb.describe_load_balancers()["LoadBalancers"]

for lb in lbs:
    lb_name = lb["LoadBalancerName"]
    lb_arn = lb["LoadBalancerArn"]
    lb_type = lb["Type"]
    lb_dns = lb["DNSName"]

    # ---------- GET TARGET GROUPS ----------
    tgs = elb.describe_target_groups(LoadBalancerArn=lb_arn)["TargetGroups"]

    for tg in tgs:
        tg_name = tg["TargetGroupName"]
        tg_arn = tg["TargetGroupArn"]
        protocol = tg["Protocol"]
        port = tg["Port"]
        hc_path = tg.get("HealthCheckPath", "")
        hc_protocol = tg["HealthCheckProtocol"]
        hc_port = tg["HealthCheckPort"]
        hc_interval = tg["HealthCheckIntervalSeconds"]

        # ---------- FIND ASSOCIATED ASG ----------
        asgs = asg.describe_auto_scaling_groups()["AutoScalingGroups"]
        matched_asg = None

        for g in asgs:
            if tg_arn in g.get("TargetGroupARNs", []):
                matched_asg = g
                break

        if matched_asg:
            asg_name = matched_asg["AutoScalingGroupName"]
            hc_type = matched_asg["HealthCheckType"]

            # Launch Template or Config
            if "LaunchTemplate" in matched_asg:
                lt_name = matched_asg["LaunchTemplate"]["LaunchTemplateName"]
                lt_version = matched_asg["LaunchTemplate"]["Version"]
                launch_type = "LaunchTemplate"
            elif "LaunchConfigurationName" in matched_asg:
                lt_name = matched_asg["LaunchConfigurationName"]
                lt_version = ""
                launch_type = "LaunchConfiguration"
            else:
                lt_name = ""
                lt_version = ""
                launch_type = ""
        else:
            asg_name = ""
            hc_type = ""
            lt_name = ""
            lt_version = ""
            launch_type = ""

        rows.append({
            "LoadBalancerName": lb_name,
            "LoadBalancerType": lb_type,
            "DNSName": lb_dns,
            "TargetGroupName": tg_name,
            "Protocol": protocol,
            "Port": port,
            "HealthCheckProtocol": hc_protocol,
            "HealthCheckPort": hc_port,
            "HealthCheckPath": hc_path,
            "HealthCheckInterval": hc_interval,
            "AutoScalingGroup": asg_name,
            "HealthCheckType (EC2/ELB)": hc_type,
            "LaunchType": launch_type,
            "LaunchTemplate/Config": lt_name,
            "LaunchTemplateVersion": lt_version
        })

# ---------- EXPORT TO EXCEL ----------
df = pd.DataFrame(rows)
df.to_excel("aws_lb_inventory.xlsx", index=False)

print("Excel generated: aws_lb_inventory.xlsx")
