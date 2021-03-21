from aws_cdk import (
    aws_codepipeline_actions as codepipeline_actions,
    aws_codepipeline as codepipeline,
    aws_codebuild as codebuild,
    aws_ecr as ecr,
    aws_ecs_patterns as ecs_patterns,
    aws_ecs as _ecs,
    aws_iam as _iam,
    aws_ec2 as _ec2,
    core as cdk
)
# For consistency with other languages, `cdk` is the preferred import name for
# the CDK's core module.  The following line also imports it as `core` for use
# with examples from the CDK Developer's Guide, which are in the process of
# being updated to use `cdk`.  You may delete this import if you don't need it.
# from aws_cdk import core


class EcsfargatecdkStack(cdk.Stack):

    def __init__(self, scope: cdk.Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # The code that defines your stack goes here
        
        vpc = _ec2.Vpc(self, "ecs-vpc",
            cidr="10.0.0.0/16",
            nat_gateways= 1,
            max_azs= 3
        )
        
        clusterAdmin = _iam.Role(self, "AdminRole",
            assumed_by= _iam.AccountRootPrincipal()
        )
        
        cluster = _ecs.Cluster(self, "ecs-cluster",
            vpc= vpc
        )
        
        logging = _ecs.AwsLogDriver(
            stream_prefix= "ecs-logs"
        )
  
        taskRole = _iam.Role(self, "ecs-taskRole",
            role_name= "ecs-taskRole",
            assumed_by= _iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
 
 
        # ECS Contructs
        
        
        executionRolePolicy = _iam.PolicyStatement(
            effect= _iam.Effect.ALLOW,
            resources= ['*'],
            actions= [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ]
        )
 
        taskDef = _ecs.FargateTaskDefinition(self, "ecs-taskdef",
            task_role= taskRole
        )

        taskDef.add_to_execution_role_policy(executionRolePolicy)
        
        container = taskDef.add_container('flask-app',
            image= _ecs.ContainerImage.from_registry("nikunjv/flask-image:blue"),
            memory_limit_mib= 256,
            cpu= 256,
            logging= logging
        )
        
        container.add_port_mappings(_ecs.PortMapping(
            container_port= 5000,
            protocol= _ecs.Protocol.TCP
            )
        )
        
        fargateService = ecs_patterns.ApplicationLoadBalancedFargateService(self, "ecs-service",
            cluster= cluster,
            task_definition= taskDef,
            public_load_balancer= True,
            desired_count= 3,
            listener_port= 80
        )
        
        scaling = fargateService.service.auto_scale_task_count(max_capacity= 6)
        
        scaling.scale_on_cpu_utilization("CpuScaling", 
            target_utilization_percent= 10,
            scale_in_cooldown= cdk.Duration.seconds(300),
            scale_out_cooldown= cdk.Duration.seconds(300)
        )
        
        # PIPELINE CONSTRUCTS
        
        # ECR Repo
        
        ecrRepo = ecr.Repository(self, "EcrRepo");
        
        gitHubSource = codebuild.Source.git_hub(
            owner= 'samuelhailemariam',
            repo= 'aws-ecs-fargate-cicd-cdk',
            webhook= True,
            webhook_filters= [
                codebuild.FilterGroup.in_event_of(codebuild.EventAction.PUSH).and_branch_is('main'),
            ]
        )
        
        # CODEBUILD - project
        
        project = codebuild.Project(self, "ECSProject",
            project_name= cdk.Aws.STACK_NAME,
            source= gitHubSource,
            environment= codebuild.BuildEnvironment(
                    build_image= codebuild.LinuxBuildImage.AMAZON_LINUX_2_2,
                    privileged= True
            ),
            environment_variables= {
                "CLUSTER_NAME": {
                    'value': cluster.cluster_name
                },
                "ECR_REPO_URI": {
                    'value': ecrRepo.repository_uri
                }
            },
            build_spec = codebuild.BuildSpec.from_object({
                'version': "0.2",
                'phases': {
                    'pre_build': {
                        'commands': [
                            'env',
                            'export TAG=${CODEBUILD_RESOLVED_SOURCE_VERSION}'
                        ]
                    },
                    'build': {
                        'commands': [
                            'cd docker-app',
                            'docker build -t $ECR_REPO_URI:$TAG .',
                            '$(aws ecr get-login --no-include-email)',
                            'docker push $ECR_REPO_URI:$TAG'
                        ]
                    },
                    'post_build': {
                        'commands': [
                            'echo "In Post-Build Stage"',
                            'cd ..',
                            "printf '[{\"name\":\"flask-app\",\"imageUri\":\"%s\"}]' $ECR_REPO_URI:$TAG > imagedefinitions.json",
                            "pwd; ls -al; cat imagedefinitions.json" 
                        ]
                    }
                },
                'artifacts': {
                    'files': [
                            'imagedefinitions.json'   
                    ]   
                }
            })
        )
        
        # PIPELINE ACTIONS
       
        sourceOutput = codepipeline.Artifact()
        buildOutput = codepipeline.Artifact()
        
        sourceAction = codepipeline_actions.GitHubSourceAction(
            action_name= 'GitHub_Source',
            owner= 'samuelhailemariam',
            repo= 'aws-ecs-fargate-cicd-cdk',
            branch= 'master',
            oauth_token= cdk.SecretValue.secrets_manager("/my/github/token"),
            output= sourceOutput
        )
       
        buildAction = codepipeline_actions.CodeBuildAction(
            action_name= 'codeBuild',
            project= project,
            input= sourceOutput,
            outputs= [buildOutput]
        )
       
        manualApprovalAction = codepipeline_actions.ManualApprovalAction(
            action_name= 'Approve'
        )
    
        deployAction = codepipeline_actions.EcsDeployAction(
            action_name= 'DeployAction',
            service= fargateService.service,
            image_file= codepipeline.ArtifactPath(buildOutput, 'imagedefinitions.json')
        )
    
        pipeline = codepipeline.Pipeline(self, "ECSPipeline")
        
        source_stage = pipeline.add_stage(
          stage_name="Source",
          actions=[sourceAction]
        )
        
        build_stage = pipeline.add_stage(
          stage_name="Build",
          actions=[buildAction]
        )
      
        approve_stage = pipeline.add_stage(
          stage_name="Approve",
          actions=[manualApprovalAction]
        )
      
        deploy_stage = pipeline.add_stage(
          stage_name="Deploy-to-ECS",
          actions=[deployAction]
        )
      
      
        ecrRepo.grant_pull_push(project.role)
        
        project.add_to_role_policy(_iam.PolicyStatement(    
            resources= [cluster.cluster_arn],
            actions= [
                "ecs:DescribeCluster",
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer"
                ]
            )
        )
      
      
        # OUTPUT
        
        cdk.CfnOutput(self, "LoadBlancer-DNS", value=fargateService.load_balancer.load_balancer_dns_name)

