import logging, json, os
import azure.functions as func
import openai
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
import tempfile
import uuid
from langchain.document_loaders import UnstructuredFileLoader
import os
from datetime import datetime, timedelta
from azure.storage.blob import BlobServiceClient, generate_blob_sas, generate_container_sas
from langchain.vectorstores import Pinecone
from langchain.vectorstores import Milvus
import pinecone
from langchain.document_loaders import PDFMinerLoader
from pymilvus import connections
from pymilvus import CollectionSchema, FieldSchema, DataType, Collection
from pymilvus import utility
import time
from langchain.vectorstores.redis import Redis

OpenAiKey = os.environ['OpenAiKey']
OpenAiApiKey = os.environ['OpenAiApiKey']
OpenAiEndPoint = os.environ['OpenAiEndPoint']
OpenAiVersion = os.environ['OpenAiVersion']
OpenAiDavinci = os.environ['OpenAiDavinci']
OpenAiService = os.environ['OpenAiService']
OpenAiDocStorName = os.environ['OpenAiDocStorName']
OpenAiDocStorKey = os.environ['OpenAiDocStorKey']
OpenAiDocConnStr = f"DefaultEndpointsProtocol=https;AccountName={OpenAiDocStorName};AccountKey={OpenAiDocStorKey};EndpointSuffix=core.windows.net"
OpenAiDocContainer = os.environ['OpenAiDocContainer']
PineconeEnv = os.environ['PineconeEnv']
PineconeKey = os.environ['PineconeKey']
VsIndexName = os.environ['VsIndexName']
VsIndexName = os.environ['VsIndexName']
RedisAddress = os.environ['RedisAddress']
RedisPassword = os.environ['RedisPassword']
OpenAiEmbedding = os.environ['OpenAiEmbedding']
RedisPort = os.environ['RedisPort']

redisUrl = "redis://default:" + RedisPassword + "@" + RedisAddress + ":" + RedisPort

def GetAllFiles(filesToProcess):
    # Get all files in the container from Azure Blob Storage
    # Create the BlobServiceClient object
    blobServiceClient = BlobServiceClient.from_connection_string(OpenAiDocConnStr)
    # Get files in the container
    containerClient = blobServiceClient.get_container_client(OpenAiDocContainer)
    blobList = containerClient.list_blobs(include='metadata')
    sas = generate_container_sas(OpenAiDocStorName, OpenAiDocContainer,account_key=OpenAiDocStorKey,  permission="r", expiry=datetime.utcnow() + timedelta(hours=3))
    files = []
    convertedFiles = {}
    for file in filesToProcess:
        files.append({
            "filename" : file['path'],
            "converted": False,
            "embedded": False,
            "fullpath": f"https://{OpenAiDocStorName}.blob.core.windows.net/{OpenAiDocContainer}/{file['path']}?{sas}",
            "converted_path": ""
            })
    # for blob in blobList:
    #     logging.info(blob.name)
    #     if not blob.name.startswith('converted/'):
    #         files.append({
    #             "filename" : blob.name,
    #             "converted": blob.metadata.get('converted', 'false') == 'true' if blob.metadata else False,
    #             "embedded": blob.metadata.get('embedded', 'false') == 'true' if blob.metadata else False,
    #             "fullpath": f"https://{OpenAiDocStorName}.blob.core.windows.net/{OpenAiDocContainer}/{blob.name}?{sas}",
    #             "converted_path": ""
    #             })
    #     else:
    #         convertedFiles[blob.name] = f"https://{OpenAiDocStorName}.blob.core.windows.net/{OpenAiDocContainer}/{blob.name}?{sas}"

    logging.info(f"Found {len(files)} files in the container")
    for file in files:
        convertedFileName = f"converted/{file['filename']}.zip"
        if convertedFileName in convertedFiles:
            file['converted'] = True
            file['converted_path'] = convertedFiles[convertedFileName]

    logging.info(files)
    return files
    #return []

def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    logging.info(f'{context.function_name} HTTP trigger function processed a request.')
    if hasattr(context, 'retry_context'):
        logging.info(f'Current retry count: {context.retry_context.retry_count}')

        if context.retry_context.retry_count == context.retry_context.max_retry_count:
            logging.info(
                f"Max retries of {context.retry_context.max_retry_count} for "
                f"function {context.function_name} has been reached")

    try:
        indexType = req.params.get('indexType')
        loadType = req.params.get('loadType')
        multiple = req.params.get('multiple')
        indexName = req.params.get('indexName')
        body = json.dumps(req.get_json())
    except ValueError:
        return func.HttpResponse(
             "Invalid body",
             status_code=400
        )

    if body:
        pinecone.init(
            api_key=PineconeKey,  # find at app.pinecone.io
            environment=PineconeEnv  # next to api key in console
        )

        # Once we can get the Milvus index running in Azure, we can use this

        # connections.connect(
        #   alias="default",
        #   host='127.0.0.1',
        #   port='19530'
        # )
        # if not utility.has_collection(VsIndexName):
        #   pkId = FieldSchema(name="pkId", dtype=DataType.INT64, is_primary=True)
        #   source = FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=500)
        #   text = FieldSchema(name="text",dtype=DataType.VARCHAR,max_length=1500)
        #   id = FieldSchema(name="id",dtype=DataType.INT64)
        #   embed = FieldSchema(name="embed",dtype=DataType.FLOAT_VECTOR,dim=1536)
        #   schema = CollectionSchema(
        #     fields=[pkId, source, text, id, embed],
        #     description="Open AI Documents"
        #   )
        #   collectionName = VsIndexName
        #   collection = Collection(name=collectionName, schema=schema, using='default')
        #   indexParam = {
        #     "metric_type":"L2",
        #     "index_type":"HNSW",
        #     "params":{"M":8, "efConstruction":64}
        #   }
        #   collection.create_index(field_name="embed", index_params=indexParam)
        #   collection.load()

        result = ComposeResponse(indexType, loadType, multiple, indexName, body)
        return func.HttpResponse(result, mimetype="application/json")
    else:
        return func.HttpResponse(
             "Invalid body",
             status_code=400
        )

def upsertMetadata(fileName, metadata):
    blobClient = BlobServiceClient.from_connection_string(OpenAiDocConnStr).get_blob_client(container=OpenAiDocContainer, blob=fileName)
    # Read metadata from the blob
    blob_metadata = blobClient.get_blob_properties().metadata
    blob_metadata.update(metadata)
    # Add metadata to the blob
    blobClient.set_blob_metadata(metadata= blob_metadata)

def ComposeResponse(indexType, loadType,  multiple, indexName, jsonData):
    values = json.loads(jsonData)['values']

    logging.info("Calling Compose Response")
    # Prepare the Output before the loop
    results = {}
    results["values"] = []

    for value in values:
        outputRecord = TransformValue(indexType, loadType,  multiple, indexName, value)
        if outputRecord != None:
            results["values"].append(outputRecord)
    return json.dumps(results, ensure_ascii=False)

# def createRedisIndex(redisConn, indexName, prefix = "embedding"):
#     text = TextField(name="text")
#     filename = TextField(name="filename")
#     embeddings = VectorField("embeddings",
#                 "HNSW", {
#                     "TYPE": "FLOAT32",
#                     "DIM": 1536,
#                     "DISTANCE_METRIC": "COSINE",
#                     "INITIAL_CAP": 3155,
#                 })
#     # Create index
#     redisConn.ft(indexName).create_index(
#         fields = [text, embeddings, filename],
#         definition = IndexDefinition(prefix=[prefix], index_type=IndexType.HASH)
#     )

def Embed(indexType, loadType, multiple, indexName,  value):
    logging.info("Embedding text")
    try:
      logging.info("Loading OpenAI")
      openai.api_type = "azure"
      openai.api_key = OpenAiKey
      openai.api_version = OpenAiVersion
      openai.api_base = f"https://{OpenAiService}.openai.azure.com"

      if (loadType == "files"):
        filesData = GetAllFiles(value)
        filesData = list(filter(lambda x : not x['embedded'], filesData))
        filesData = list(map(lambda x: {'filename': x['filename']}, filesData))

        logging.info(f"Found {len(filesData)} files to embed")
        uResultNs = uuid.uuid4()
        for file in filesData:
            logging.info(f"Adding {file['filename']} to Process")
            fileName = file['filename']
            # Check the file extension
            if fileName.endswith('.txt'):
                logging.info("Embedding text file")
                # Read the file from Blob Storage
                blobClient = BlobServiceClient.from_connection_string(OpenAiDocConnStr).get_blob_client(container=OpenAiDocContainer, blob=fileName)
                fileContent = blobClient.download_blob().readall().decode('utf-8')

                uResult = uuid.uuid4()
                downloadPath = os.path.join(tempfile.gettempdir(), uResult.hex + ".txt")
                os.makedirs(os.path.dirname(tempfile.gettempdir()), exist_ok=True)
                try:
                    with open(downloadPath, "wb") as file:
                        file.write(bytes(fileContent, 'utf-8'))
                except Exception as e:
                    logging.error(e)

                logging.info("File created")
                textSplitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=0)
                loader = UnstructuredFileLoader(downloadPath)
                rawDocs = loader.load()
                docs = textSplitter.split_documents(rawDocs)
                logging.info("Docs " + str(len(docs)))
                #embeddings = OpenAIEmbeddings(openai_api_key=OpenAiApiKey)
                embeddings = OpenAIEmbeddings(document_model_name="text-embedding-ada-002",
                                                chunk_size=1,
                                                openai_api_key=OpenAiKey)
                if indexType == 'pinecone':
                    Pinecone.from_documents(docs, embeddings, index_name=VsIndexName, namespace=uResultNs.hex)
                elif indexType == "redis":
                    redisConnection = Redis(redis_url=redisUrl, index_name=uResultNs.hex, embedding_function=embeddings)
                    # if not (redisConnection.ft(uResult.hex).info()):
                    #     createRedisIndex(redisConnection, uResult.hex)
                    Redis.from_documents(docs, embeddings, redis_url=redisUrl, index_name=uResultNs.hex)
                elif indexType == 'milvus':
                    milvus = Milvus(connection_args={"host": "127.0.0.1", "port": "19530"},
                                    collection_name=VsIndexName, text_field="text", embedding_function=embeddings)
                    Milvus.from_documents(docs,embeddings)

                # Embed the file
                #data = chunk_and_embed(fileContent, fileName)
                # Set the document in Redis
                #set_document(data)
            else:
                logging.info("Embedding Non-text file")
                blobServiceClient = BlobServiceClient.from_connection_string(OpenAiDocConnStr)
                blobClient = blobServiceClient.get_blob_client(container=OpenAiDocContainer, blob=fileName)
                readBytes = blobClient.download_blob().readall()
                uResult = uuid.uuid4()
                downloadPath = os.path.join(tempfile.gettempdir(), fileName)
                os.makedirs(os.path.dirname(tempfile.gettempdir()), exist_ok=True)
                try:
                    with open(downloadPath, "wb") as file:
                        file.write(readBytes)
                except Exception as e:
                    logging.error(e)

                logging.info("File created")
                textSplitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=0)
                docs = []
                loader = PDFMinerLoader(downloadPath)
                rawDocs = loader.load()
                docs = textSplitter.split_documents(rawDocs)
                #embeddings = OpenAIEmbeddings(openai_api_key=OpenAiApiKey)
                embeddings = OpenAIEmbeddings(document_model_name="text-embedding-ada-002",
                                                chunk_size=1,
                                                openai_api_key=OpenAiKey)
                if indexType == 'pinecone':
                    pineconeDb = Pinecone.from_documents(docs, embeddings, index_name=VsIndexName, namespace=uResultNs.hex)
                elif indexType == "redis":
                    redisConnection = Redis(redis_url=redisUrl, index_name=uResultNs.hex, embedding_function=embeddings)
                    Redis.from_documents(docs, embeddings, redis_url=redisUrl, index_name=uResultNs.hex)
                elif indexType == 'milvus':
                    milvusDb = Milvus.from_documents(docs,embeddings)
            logging.info("Upsert metadata")
            upsertMetadata(fileName, {'embedded': 'true', 'namespace': uResultNs.hex, 'indexType': indexType, "indexName": indexName})
            logging.info("Sleeping")
            time.sleep(15)
        return "Success"
      elif (loadType == "webpages"):
        logging.info("Embedding Webpages")
        return "Success"
      elif (loadType == "iFixIt"):
        logging.info("Embedding iFixIt")
        return "Success"
    except Exception as e:
      logging.error(e)
      return func.HttpResponse(
            "Error getting files",
            status_code=500
      )

def TransformValue(indexType, loadType,  multiple, indexName, record):
    logging.info("Calling Transform Value")
    try:
        recordId = record['recordId']
    except AssertionError  as error:
        return None

    # Validate the inputs
    try:
        assert ('data' in record), "'data' field is required."
        data = record['data']
        assert ('text' in data), "'text' field is required in 'data' object."

    except KeyError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "KeyError:" + error.args[0] }   ]
            })
    except AssertionError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "AssertionError:" + error.args[0] }   ]
            })
    except SystemError as error:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "SystemError:" + error.args[0] }   ]
            })

    try:
        # Getting the items from the values/data/text
        value = data['text']

        summaryResponse = Embed(indexType, loadType,  multiple, indexName, value)
        return ({
            "recordId": recordId,
            "data": {
                "text": summaryResponse
                    }
            })

    except:
        return (
            {
            "recordId": recordId,
            "errors": [ { "message": "Could not complete operation for record." }   ]
            })
