#!/usr/bin/env bash
# Bootstrap da infra AWS do quote-agent (rodar UMA vez, com credenciais AWS ativas).
# Cria: ECR, OIDC provider do GitHub, role de deploy, role/perfil da EC2,
# security group, EC2 t3.small (AL2023 + docker), Elastic IP, e envia
# docker-compose/Caddyfile/.env para /opt/quote-agent via SSM.
# Idempotente: recursos existentes são reaproveitados.
#
# Uso:  ./infra/aws-bootstrap.sh
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
APP="quote-agent"
GH_REPO="${GH_REPO:-luispavila/quote-agent}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

export AWS_DEFAULT_REGION="$REGION"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "==> Conta AWS: $ACCOUNT_ID | Região: $REGION | Repo GitHub: $GH_REPO"

# ---------- 1. ECR ----------
if ! aws ecr describe-repositories --repository-names "$APP" >/dev/null 2>&1; then
  aws ecr create-repository --repository-name "$APP" \
    --image-scanning-configuration scanOnPush=true >/dev/null
  echo "==> ECR criado: $APP"
else
  echo "==> ECR já existe: $APP"
fi
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$APP"

# ---------- 2. OIDC provider do GitHub ----------
OIDC_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider \
    --url "https://token.actions.githubusercontent.com" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd >/dev/null
  echo "==> OIDC provider do GitHub criado"
else
  echo "==> OIDC provider do GitHub já existe"
fi

# ---------- 3. Role de deploy (GitHub Actions) ----------
DEPLOY_ROLE="$APP-github-deploy"
TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "$OIDC_ARN"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:$GH_REPO:*"}
    }
  }]
}
JSON
)
if ! aws iam get-role --role-name "$DEPLOY_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$DEPLOY_ROLE" \
    --assume-role-policy-document "$TRUST" >/dev/null
  echo "==> Role de deploy criada: $DEPLOY_ROLE"
else
  aws iam update-assume-role-policy --role-name "$DEPLOY_ROLE" --policy-document "$TRUST"
  echo "==> Role de deploy já existia (trust atualizada)"
fi

DEPLOY_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "EcrAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Sid": "EcrPush", "Effect": "Allow", "Action": [
      "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload",
      "ecr:PutImage", "ecr:UploadLayerPart", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"
    ], "Resource": "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$APP"},
    {"Sid": "SsmDeploy", "Effect": "Allow", "Action": "ssm:SendCommand", "Resource": [
      "arn:aws:ec2:$REGION:$ACCOUNT_ID:instance/*",
      "arn:aws:ssm:$REGION::document/AWS-RunShellScript"
    ], "Condition": {"StringEquals": {"aws:ResourceTag/App": "$APP"}}},
    {"Sid": "SsmDeployDoc", "Effect": "Allow", "Action": "ssm:SendCommand",
     "Resource": "arn:aws:ssm:$REGION::document/AWS-RunShellScript"},
    {"Sid": "SsmRead", "Effect": "Allow", "Action": [
      "ssm:GetCommandInvocation", "ssm:ListCommands", "ssm:ListCommandInvocations"
    ], "Resource": "*"}
  ]
}
JSON
)
aws iam put-role-policy --role-name "$DEPLOY_ROLE" --policy-name deploy --policy-document "$DEPLOY_POLICY"

# ---------- 4. Role e instance profile da EC2 ----------
EC2_ROLE="$APP-ec2"
EC2_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if ! aws iam get-role --role-name "$EC2_ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$EC2_ROLE" --assume-role-policy-document "$EC2_TRUST" >/dev/null
  echo "==> Role da EC2 criada: $EC2_ROLE"
fi
aws iam attach-role-policy --role-name "$EC2_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name "$EC2_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
if ! aws iam get-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$EC2_ROLE" --role-name "$EC2_ROLE"
  echo "==> Instance profile criado"
  sleep 10  # propagação do IAM
fi

# ---------- 5. Security group ----------
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values="$APP" Name=vpc-id,Values="$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name "$APP" \
    --description "quote-agent web" --vpc-id "$VPC_ID" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null
  echo "==> Security group criado: $SG_ID (80/443; SSH não — acesso via SSM)"
else
  echo "==> Security group já existe: $SG_ID"
fi

# ---------- 6. EC2 ----------
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters Name=tag:App,Values="$APP" Name=instance-state-name,Values=pending,running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")
if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
  AMI_ID=$(aws ssm get-parameter \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query 'Parameter.Value' --output text)
  INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
    --iam-instance-profile Name="$EC2_ROLE" \
    --security-group-ids "$SG_ID" \
    --user-data "file://$REPO_ROOT/infra/aws/ec2-user-data.sh" \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$APP},{Key=App,Value=$APP}]" \
    --query 'Instances[0].InstanceId' --output text)
  echo "==> EC2 criada: $INSTANCE_ID ($INSTANCE_TYPE)"
  aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
else
  echo "==> EC2 já existe: $INSTANCE_ID"
fi

# ---------- 7. Elastic IP ----------
EIP_ALLOC=$(aws ec2 describe-addresses --filters Name=tag:App,Values="$APP" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || echo "None")
if [ "$EIP_ALLOC" = "None" ] || [ -z "$EIP_ALLOC" ]; then
  EIP_ALLOC=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=App,Value=$APP}]" \
    --query AllocationId --output text)
  echo "==> Elastic IP alocado"
fi
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" >/dev/null
EIP=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp' --output text)
echo "==> Elastic IP: $EIP"

# ---------- 8. Enviar compose/Caddyfile/.env via SSM ----------
echo "==> Aguardando a instância registrar no SSM…"
for i in $(seq 1 30); do
  PING=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "None")
  [ "$PING" = "Online" ] && break
  sleep 10
done
[ "$PING" = "Online" ] || { echo "❌ instância não registrou no SSM"; exit 1; }

COMPOSE_B64=$(base64 < "$REPO_ROOT/docker-compose.yml" | tr -d '\n')
CADDY_B64=$(base64 < "$REPO_ROOT/infra/Caddyfile" | tr -d '\n')
ENV_FILE="$REPO_ROOT/.env.production"
if [ -f "$ENV_FILE" ]; then
  ENV_B64=$(base64 < "$ENV_FILE" | tr -d '\n')
  ENV_CMD="echo $ENV_B64 | base64 -d > /opt/quote-agent/.env"
  echo "==> Enviando .env.production para a instância"
else
  ENV_CMD="true"
  echo "⚠️  $ENV_FILE não existe — crie o .env na instância antes do primeiro deploy"
fi

CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=[
    \"mkdir -p /opt/quote-agent/infra\",
    \"echo $COMPOSE_B64 | base64 -d > /opt/quote-agent/docker-compose.yml\",
    \"echo $CADDY_B64 | base64 -d > /opt/quote-agent/infra/Caddyfile\",
    \"$ENV_CMD\"
  ]" --query 'Command.CommandId' --output text)
aws ssm wait command-executed --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" || true

DEPLOY_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$DEPLOY_ROLE"
cat <<EOF

============================================================
✅ Bootstrap concluído.

  ECR:          $ECR_URI
  EC2:          $INSTANCE_ID  ($INSTANCE_TYPE)
  IP público:   $EIP
  Role deploy:  $DEPLOY_ROLE_ARN

Agora configure os secrets do GitHub:

  gh secret set AWS_ROLE_ARN      --repo $GH_REPO --body "$DEPLOY_ROLE_ARN"
  gh secret set EC2_INSTANCE_ID   --repo $GH_REPO --body "$INSTANCE_ID"
  gh secret set PUBLIC_BASE_URL   --repo $GH_REPO --body "http://$EIP"

Para HTTPS sem domínio próprio, use sslip.io:
  DOMAIN=$(echo "$EIP" | tr . -).sslip.io  (no .env da instância)
  e PUBLIC_BASE_URL=https://$(echo "$EIP" | tr . -).sslip.io

Depois: git push na main dispara o deploy.
============================================================
EOF
