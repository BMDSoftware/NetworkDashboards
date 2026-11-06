$(document).ready(function () {

    $('#uploadForm').find('input[type="file"]').val('');

    $('[data-toggle="popover"]').popover({
        html: true,
        placement: 'left',
        trigger: 'hover focus'
    });

    $('#uploadForm').on('submit', function (e) {
        var fileInput = $(this).find('input[type="file"][name="results_file"]');

        if (fileInput.length > 0 && fileInput.val() !== '') {
            e.preventDefault();
            
            var formData = new FormData(this);
            var submitBtn = $(this).find('button[type="submit"]');
            
            submitBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Uploading...');

            $.ajax({
                url: window.location.pathname + '?ajax=true',
                type: 'POST',
                data: formData,
                processData: false,
                contentType: false,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                },
                success: function (response) {
                    if (response.success) {
                        window.location.reload();
                    } else {
                        window.location.href = window.location.pathname;
                    }
                },
                error: function () {
                    alert('Error in uploading the file. Verify the server logs.');
                    submitBtn.prop('disabled', false).html('<i class="fas fa-upload"></i> Upload');
                }
            });
        }
    });

    initTableProgressPolling();
});

function initTableProgressPolling() {
    var processingCells = $('td[data-processing="yes"]');
    
    processingCells.each(function () {
        var cell = $(this);
        var uploadId = cell.attr('data-upload-id');
        if (!uploadId) return;

        var progressBar = $('#table-bar-' + uploadId);
        var stepText = $('#table-step-' + uploadId);

        var savedPercent = sessionStorage.getItem('progress_percent_' + uploadId);
        var savedStep = sessionStorage.getItem('progress_step_' + uploadId);
        
        if (savedPercent && progressBar.length > 0) {
            progressBar.css('width', savedPercent + '%').text(savedPercent + '%');
        }
        if (savedStep && stepText.length > 0) {
            stepText.text(savedStep);
        }

        var pathParts = window.location.pathname.split('/').filter(Boolean);
        var dataSourceHash = pathParts[pathParts.length - 1];
        var statusUrl = '/uploader/' + dataSourceHash + '/upload/' + uploadId + '/status/';

        var updateStatus = function () {
            $.ajax({
                url: statusUrl,
                dataType: 'json',
                success: function (data) {
                    if (data.percent !== undefined && progressBar.length > 0) {
                        progressBar.css('width', data.percent + '%').text(data.percent + '%');
                        sessionStorage.setItem('progress_percent_' + uploadId, data.percent);
                    }
                    if (data.step_message && stepText.length > 0) {
                        stepText.text(data.step_message);
                        sessionStorage.setItem('progress_step_' + uploadId, data.step_message);
                    }

                    const isFailed = data.failed || (data.status && data.status.toLowerCase() === 'failed');

                    if (data.completed || isFailed) {
                        clearInterval(interval);
                        
                        sessionStorage.removeItem('progress_percent_' + uploadId);
                        sessionStorage.removeItem('progress_step_' + uploadId);

                        if (progressBar.length > 0) {
                            progressBar.css('width', '100%').text('100%');
                            
                            if (isFailed) {
                                progressBar.removeClass('bg-info bg-success bg-warning').addClass('bg-danger');
                                if (stepText.length > 0) stepText.text('Upload failed!');
                            } else if (data.has_warnings) {
                                progressBar.removeClass('bg-info bg-danger bg-success').addClass('bg-warning');
                                if (stepText.length > 0) stepText.text('Completed with warnings.');
                            } else {
                                progressBar.removeClass('bg-info bg-danger bg-warning').addClass('bg-success');
                                if (stepText.length > 0) stepText.text('Upload completed!');
                            }
                        }

                        setTimeout(function () {
                            window.location.href = window.location.pathname;
                        }, 800);
                    }
                },
                error: function (xhr) {
                    console.warn("Connection lost (HTTP " + xhr.status + "). Retrying status check in 3 seconds...");
                }
            });
        };

        updateStatus();
        var interval = setInterval(updateStatus, 3000);
    });
}