#!/bin/bash
# MITS ONE — AWS Serverless Lambda Deployment Script
set -e

APP_NAME="mits-one-attendance"
REGION="ap-southeast-2"
PROFILE="${AWS_PROFILE:-agent}"
ROLE_ARN="arn:aws:iam::573993263066:role/mits-one-lambda-role"
BUNDLE="mits-one-lambda.zip"
BUILD_DIR="build_lambda"

echo "======================================================"
echo "  MITS ONE — AWS Serverless Lambda Deploy"
echo "  Region: $REGION | Profile: $PROFILE"
echo "======================================================"

# Step 1: Verify identity
echo ""
echo "▶ Checking AWS credentials..."
aws sts get-caller-identity --profile "$PROFILE"

# Step 2: Build package
echo ""
echo "▶ Building deployment bundle..."
rm -rf "$BUILD_DIR" "$BUNDLE"
mkdir -p "$BUILD_DIR"

# Install production dependencies for Lambda Linux x86_64
pip3 install --target "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  flask requests

# Copy application sources
cp server.py gems_api_working.py lambda_function.py index.html "$BUILD_DIR/"

# Create zip
cd "$BUILD_DIR"
zip -q -r "../$BUNDLE" .
cd ..
rm -rf "$BUILD_DIR"
echo "  Bundle size: $(du -sh $BUNDLE | cut -f1)"

# Step 3: Deploy or Update Lambda
echo ""
if aws lambda get-function --function-name "$APP_NAME" --region "$REGION" --profile "$PROFILE" >/dev/null 2>&1; then
  echo "▶ Updating existing Lambda function code..."
  aws lambda update-function-code \
    --function-name "$APP_NAME" \
    --zip-file "fileb://$BUNDLE" \
    --region "$REGION" \
    --profile "$PROFILE" >/dev/null
else
  echo "▶ Creating new Lambda function..."
  aws lambda create-function \
    --function-name "$APP_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file "fileb://$BUNDLE" \
    --timeout 30 \
    --memory-size 512 \
    --architectures x86_64 \
    --region "$REGION" \
    --profile "$PROFILE" >/dev/null

  aws lambda wait function-active-v2 --function-name "$APP_NAME" --region "$REGION" --profile "$PROFILE"

  aws lambda create-function-url-config \
    --function-name "$APP_NAME" \
    --auth-type NONE \
    --cors '{"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"]}' \
    --region "$REGION" \
    --profile "$PROFILE" >/dev/null

  aws lambda add-permission \
    --function-name "$APP_NAME" \
    --statement-id FunctionURLAllowPublicAccess \
    --action lambda:InvokeFunctionUrl \
    --principal "*" \
    --function-url-auth-type NONE \
    --region "$REGION" \
    --profile "$PROFILE" >/dev/null

  aws lambda add-permission \
    --function-name "$APP_NAME" \
    --statement-id FunctionInvokeAllowPublicAccess \
    --action lambda:InvokeFunction \
    --principal "*" \
    --region "$REGION" \
    --profile "$PROFILE" >/dev/null
fi

# Step 4: Get Function URL
FUNCTION_URL=$(aws lambda get-function-url-config \
  --function-name "$APP_NAME" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query "FunctionUrl" \
  --output text)

echo ""
echo "======================================================"
echo "  ✅ DEPLOYED SUCCESSFULLY TO AWS!"
echo "  🌐 Live URL: $FUNCTION_URL"
echo "======================================================"
